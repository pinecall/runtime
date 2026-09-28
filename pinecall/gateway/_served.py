"""The calls this gateway serves: opened, handed on, sealed, and reaped when their worker dies."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Literal

from livekit import api

from pinecall.channels.rooms import room_closed, rooms_with_an_agent
from pinecall.domain.agent import AgentConfig, Model
from pinecall.domain.call import CallContext
from pinecall.domain.errors import Conflict, NotAvailable, PinecallError, QuotaExhausted
from pinecall.domain.names import CHANNELS_WITH_A_NUMBER, Env, JsonObject
from pinecall.domain.scope import Scope
from pinecall.evals import judges
from pinecall.gateway._call_setup import exhausted, keys_of
from pinecall.gateway._sockets import Process, Registration, SocketId, Sockets, orgs_own
from pinecall.log import facts, queries
from pinecall.log.logs import Log, Logs, Subscription, arrival_entry
from pinecall.log.reduce import reduce
from pinecall.log.store import Store
from pinecall.postgres.pool import Pool
from pinecall.process.connections import Connections
from pinecall.providers import catalog, credentials, prices
from pinecall.providers.build import Running
from pinecall.providers.catalog import Providers
from pinecall.providers.credentials import Keyring, thinking
from pinecall.retrieval import extraction, lookups, memory
from pinecall.retrieval.embed import Embedder
from pinecall.retrieval.lookups import OnTheCall
from pinecall.session.session import Session
from pinecall.session.tools import ToolCalls
from pinecall.tenancy import admission, orgs, vault
from pinecall.tenancy.codes import Codes
from pinecall.wire.commands import DevAnswer
from pinecall.wire.events import (
    CallAttached,
    CallClaimed,
    CallEnded,
    CallScore,
    CallStarted,
    CallSummary,
    ErrorEvent,
    MemoryOps,
)
from pinecall.wire.frames import Command, Entry
from pinecall.wire.parts import EndReason, MemoryOp
from pinecall.wire.rest.calls import LookupRequest, SealCallRequest
from pinecall.wire.state import AgentTurn

type Send = Callable[[Entry], Awaitable[None]]


logger = logging.getLogger(__name__)


NO_AGENT = "no app is holding agent {slug}"


NOT_THAT_APP = "app {app} is not holding agent {slug}: it disconnected, or it never held it"


NO_UNCLAIMED = (
    "agent {slug} is held only by apps that take no call they did not open: run `pinecall start`"
)


JUDGING_OFF = "this org's calls are not judged at hang-up: POST /v1/evals/judge/{call} judges one"


A_RUN_JUDGES_IT = "an eval run opened this call, and its own judges scored it in the run's matrix"


NO_JUDGE = "this box's providers configuration names no judge model"


NO_CEILING = "PINECALL_JUDGE_CEILING_USD is zero, so no judge model may be asked"


JUDGING_BROKE = "judging this call failed: {broke}"


NOT_JUDGED_REAPED = (
    "the worker holding this call went away before it could end it, and the platform sealed the "
    "log: there was no session left to judge"
)


NOTHING_SAID = "no reply"


REMEMBER_FAILED = "the call was not written into memory: {why}"


# What a hang-up waits for the one model call that writes memory, unless the settings say.
REMEMBER_BUDGET_S = 8.0


# Entries a taking-over socket is rebuilt from; the prompt is not, the log keeps its hash alone.
STARTED = "call.started"


CLAIMED = "call.claimed"


# Well over livekit's empty_timeout (60 s): the empty room is the signal, this is the margin.
QUIET_S = 5 * 60.0


REAPED_EVERY_S = 60.0


AT_MOST = 100


# How long a WhatsApp thread waits for its contact before it is closed.
A_THREAD_WAITS_S = 2 * 60 * 60.0


REAPED = "sealed %s: no agent is in its room and it has said nothing for %.0f s"


# A call belongs to its agent, not to a socket: `app` is None while it waits for one.
@dataclass(frozen=True)
class Served:
    """A call this gateway serves: the socket it reaches, its log, its commands, its tools."""

    agent: str
    scope: Scope
    app: SocketId | None
    log: Log
    entries: Subscription
    # A None ends the worker's command stream.
    commands: asyncio.Queue[Command | None]
    context: CallContext
    config: AgentConfig
    tools: ToolCalls
    # A written call runs here; a voice call runs in a worker.
    session: Session | None = None

    @property
    def call(self) -> str:
        """The call's id."""
        return self.context.call


# Not durable on purpose: it says which sockets are open now; the log is the record.
class ServedCalls:
    """The open app sockets and the calls served to them."""

    def __init__(self) -> None:
        """Nothing open."""
        self.sockets: dict[SocketId, Send] = {}
        self.processes: dict[SocketId, Process] = {}
        self.calls: dict[str, Served] = {}
        self.pending_answers: dict[str, asyncio.Future[DevAnswer]] = {}
        # asyncio keeps weak references to tasks: the pumps are held here.
        self.pumps: set[asyncio.Task[None]] = set()

    def connect(self, process: Process, send: Send) -> None:
        """An app socket opened."""
        self.sockets[process.app] = send
        self.processes[process.app] = process

    def disconnect(self, app: SocketId) -> None:
        """An app socket closed; its calls go on until another socket takes them."""
        self.sockets.pop(app, None)
        self.processes.pop(app, None)

    def named(self, app: SocketId, host: str | None) -> None:
        """The machine the app said it runs on."""
        process = self.processes.get(app)
        if process is not None and host is not None:
            self.processes[app] = replace(process, host=host)

    def processes_of(self, org: str, env: Env) -> list[Process]:
        """The org's sockets in the world, oldest first."""
        mine = (
            value
            for value in self.processes.values()
            if value.scope.org == org and value.scope.env == env
        )
        return sorted(mine, key=lambda one: one.connected_at)

    # dev.request is never stored: it goes straight down the socket.
    async def tell(self, app: SocketId, entry: Entry) -> bool:
        """Send an entry to one socket; False when it is not open here."""
        send = self.sockets.get(app)
        if send is None:
            return False
        await send(entry)
        return True

    def ask(self, params: str) -> asyncio.Future[DevAnswer]:
        """A future for the answer to a dev.request."""
        answer: asyncio.Future[DevAnswer] = asyncio.get_running_loop().create_future()
        self.pending_answers[params] = answer
        return answer

    def dev_answered(self, answer: DevAnswer) -> bool:
        """Hand an app's dev.answer to the door waiting for it; False when nobody waits."""
        waiting = self.pending_answers.pop(answer.id, None)
        if waiting is None or waiting.done():
            return False
        waiting.set_result(answer)
        return True

    def serve(self, served: Served) -> None:
        """Serve the call to the socket its door chose; a call already served stays as it is."""
        if served.call in self.calls:
            return
        self.calls[served.call] = served
        self._feed(served)

    # Synchronous, so two sockets taking one parked call cannot both have it.
    def attach(self, call: str, app: SocketId | None) -> Served | None:
        """Move the call to this socket (None parks it); None when nothing moved."""
        served = self.calls.get(call)
        if served is None or served.app == app:
            return None
        served.entries.close()
        moved = replace(served, app=app, entries=served.log.fanout.subscribe())
        self.calls[call] = moved
        self._feed(moved)
        return moved

    def bound_to(self, app: SocketId) -> list[str]:
        """The calls this socket serves."""
        return [call for call, served in self.calls.items() if served.app == app]

    def parked(self, scope: Scope, agent: str) -> list[str]:
        """The calls of the agent in the scope that wait for a socket."""
        return [
            call
            for call, served in self.calls.items()
            if served.app is None and served.agent == agent and served.scope == scope
        ]

    # The concurrent calls quota counts these, never head rows: a dead worker's row would count
    # for ever.
    def running(self, org: str) -> int:
        """How many of the org's calls are open here."""
        return sum(1 for served in self.calls.values() if served.scope.org == org)

    def commanded(self, call: str | None, agent: str, command: Command) -> bool:
        """Queue an app's command for the worker running the call; False when it is not here."""
        served = None if call is None else self.calls.get(call)
        if served is None or served.agent != agent or served.session is not None:
            return False
        served.commands.put_nowait(command)
        return True

    def close(self, call: str) -> None:
        """Forget a finished call: its entries end, and its worker's command stream."""
        served = self.calls.pop(call, None)
        if served is None:
            return
        served.entries.close()
        served.commands.put_nowait(None)

    def _feed(self, served: Served) -> None:
        send = None if served.app is None else self.sockets.get(served.app)
        if send is None:
            served.entries.close()
            return
        pump = asyncio.ensure_future(_pumped(served.entries, send))
        self.pumps.add(pump)
        pump.add_done_callback(self.pumps.discard)


@dataclass(frozen=True)
class Serving:
    """What serves a call: the process's connections, the logs, and the calls live in it."""

    connections: Connections
    logs: Logs
    live: ServedCalls
    # None when the box embeds nothing: every lookup finds nothing and no hang-up remembers.
    embedder: Embedder | None


# A call opened with a key goes to that key's scope; one that rang, to the caller's phone
# owner, else to the line.
def serving_agent(
    sockets: Sockets, scope: Scope, agent: str, app: SocketId | None, context: CallContext
) -> Registration | None:
    """The socket a new call of the agent reaches."""
    if app is None and context.route.channel in CHANNELS_WITH_A_NUMBER:
        return sockets.taking(orgs_own(scope), agent, context.caller)
    return sockets.serving(scope, agent, app)


def served_call(
    serving: Serving,
    owner: SocketId | None,
    context: CallContext,
    config: AgentConfig,
    scope: Scope,
) -> Served:
    """Serve the call to its socket, or park it for the next one; before its first entry."""
    log = serving.logs.writing(context.call, config.slug)
    served = Served(
        agent=config.slug,
        scope=scope,
        app=owner,
        log=log,
        entries=log.fanout.subscribe(),
        commands=asyncio.Queue(),
        context=context,
        config=config,
        tools=ToolCalls(config, log.append),
    )
    serving.live.serve(served)
    return serving.live.calls[context.call]


async def opened(log: Log, context: CallContext, agent: str) -> None:
    """call.ringing for an inbound call; an outbound one has its call.dialing already."""
    if context.direction == "outbound":
        return
    kind, data = arrival_entry(context, context.route.number or agent)
    await log.append(kind, data)


# The quotas are read per lookup, so a plan changed mid-call applies from the next turn.
async def looked_up(serving: Serving, served: Served, request: LookupRequest) -> JsonObject:
    """Recall or search for a call served here, written on its log, answered as the model reads."""
    pool = serving.connections.pool
    quotas = await admission.quotas_of(pool, served.scope.org, served.scope.env)
    on_the_call = OnTheCall(
        scope=served.scope,
        context=served.context,
        config=served.config,
        log=served.log,
        now=_now(serving),
    )
    return await lookups.lookup(pool, serving.embedder, on_the_call, request, quotas=quotas)


# Runs between call.ended and call.summary. The org's keys are read now, so a key rotated during
# the call is the one used. A refusal at the cap goes on the agent's log, and the call's says an
# empty remember; anything that breaks is an entry, and the call still seals.
async def remembered(serving: Serving, served: Served) -> MemoryOp | None:
    """What the call taught its contact's memory, written; None when the agent keeps nothing."""
    pool = serving.connections.pool
    entries = await serving.logs.store.whole(served.call)
    heard = lookups.heard_in(served.context, served.config, entries, at=_now(serving))
    if heard is None or serving.embedder is None:
        return None
    try:
        await admission.admit_memory(
            pool,
            served.scope.org,
            served.scope.env,
            kept=await memory.kept(pool, served.scope.org, served.scope.env),
        )
    except QuotaExhausted as refused:
        await exhausted(serving.logs, served.scope.org, served.agent, refused)
        op = MemoryOp(op="remember", contact=heard.contact, facts=[], took_ms=0.0)
        await served.log.append("memory.ops", MemoryOps(ops=[op]).written())
        return op
    budget = serving.connections.settings.remember_budget_s or REMEMBER_BUDGET_S
    try:
        async with asyncio.timeout(budget):
            configured = await catalog.providers(pool)
            keys = await keys_of(pool, serving.connections.vault, served.scope)
            model = thinking(served.config, configured, keys)
            op = await extraction.remember(pool, serving.embedder, model, served.scope, heard)
    except Exception as broke:
        # Memory is a courtesy to the next call; this one ends all the same.
        logger.warning("call %s was not written into memory", served.call, exc_info=True)
        why = REMEMBER_FAILED.format(why=str(broke) or type(broke).__name__)
        failed = ErrorEvent(code="remember_failed", message=why, recoverable=True)
        await served.log.append("error", failed.written())
        return None
    await served.log.append("memory.ops", MemoryOps(ops=[op]).written())
    return op


async def attach(live: ServedCalls, store: Store, call: str, app: SocketId) -> Entry | None:
    """Give the call to this socket: call.attached first, then the tools still waiting."""
    served = live.attach(call, app)
    if served is None:
        return None
    entries = await store.whole(call)
    started = next((entry for entry in entries if entry.type == STARTED), None)
    if started is None:
        return None
    claimed = next(
        (str(entry.data["code"]) for entry in reversed(entries) if entry.type == CLAIMED), None
    )
    data = CallAttached(
        app=app,
        started=CallStarted.model_validate(started.data),
        state=reduce(entries).app_state,
        seq=entries[-1].seq,
        claimed=claimed,
    )
    entry = await served.log.append("call.attached", data.written())
    # On the same queue, after call.attached, so the result lands on the call id still awaited.
    for waiting in served.tools.pending():
        served.entries.offer(waiting)
    return entry


async def parked_calls_of(
    live: ServedCalls, store: Store, scope: Scope, slug: str, app: SocketId
) -> list[str]:
    """Give every parked call of the agent in the scope to this socket."""
    parked = live.parked(scope, slug)
    return [call for call in parked if await attach(live, store, call, app) is not None]


# Each call of a leaving socket goes where a new call would, or waits parked.
async def handed_on(
    live: ServedCalls, store: Store, sockets: Sockets, calls: Iterable[str]
) -> tuple[int, int]:
    """Hand the calls on; how many were handed and how many parked."""
    handed = parked = 0
    for call in calls:
        served = live.calls.get(call)
        if served is None:
            continue
        taking = sockets.serving(served.scope, served.agent, None)
        if taking is not None and await attach(live, store, call, taking.owner) is not None:
            handed += 1
        else:
            live.attach(call, None)
            parked += 1
    return handed, parked


async def claim_code(
    codes: Codes, served: Served, code: str, *, via: Literal["keypad", "agent"]
) -> bool:
    """Tie the call to the page showing the code; False when no page waits on it."""
    issued = await codes.claim(served.scope.env, served.agent, code, served.call)
    if issued is None:
        return False
    await served.log.append("call.claimed", CallClaimed(code=code, via=via).written())
    return True


# One end for every call: a worker's, a written one's, and one the reaper finishes.
async def sealed(
    serving: Serving, served: Served, sealing: SealCallRequest, *, lent: Sequence[str] = ()
) -> None:
    """Remember, price the call, write its summary and its score, seal the log, let it go."""
    await remembered(serving, served)
    await summed_up(serving.connections.pool, serving.logs.store, served.log, sealing)
    if lent:
        await facts.lent(serving.connections.pool, served.call, lent)
    if served.context.run is not None:
        score = CallScore(judges=[], judge_calls=0, not_judged=A_RUN_JUDGES_IT)
    elif not await orgs.judged(serving.connections.pool, served.scope.org):
        score = CallScore(judges=[], judge_calls=0, not_judged=JUDGING_OFF.format(call=served.call))
    else:
        entries = await serving.logs.store.whole(served.call)
        score = await judged_call(serving.connections, entries, served.config)
    await served.log.append("call.score", score.written())
    serving.logs.forget(served.call)
    serving.live.close(served.call)


async def summed_up(pool: Pool, store: Store, log: Log, sealing: SealCallRequest) -> None:
    """call.summary: how the call ended, what it used, what that cost."""
    entries = await store.whole(log.name)
    ended = next((entry for entry in reversed(entries) if entry.type == "call.ended"), None)
    over = None if ended is None else CallEnded.model_validate(ended.data)
    state = reduce(entries)
    summary = CallSummary(
        reason="error" if over is None else over.reason,
        outcome=sealing.outcome,
        duration_s=0.0 if over is None else over.duration_s,
        turns=sum(1 for turn in state.turns if isinstance(turn, AgentTurn)),
        usage=sealing.usage,
        cost=prices.cost(sealing.usage, await catalog.providers(pool)),
        recording=sealing.recording,
    )
    await log.append("call.summary", summary.written())


# A killed job writes no call.ended, and nothing else would close its log.
async def reaped(serving: Serving, server: api.LiveKitAPI, now: float) -> list[str]:
    """Seal the quiet calls nothing runs any more, and say which."""
    sealed_now: list[str] = []
    quiet = await queries.unsealed_spoken(serving.connections.pool, now - QUIET_S, limit=AT_MOST)
    existing: set[str] = set()
    if quiet:
        existing = await rooms_with_an_agent(server, [item.call for item in quiet])
    for orphan in quiet:
        if orphan.call not in existing and await _finished(serving, orphan, "drained"):
            # The room goes too, so whoever is still in it hears the call end.
            await room_closed(server, orphan.call)
            logger.warning(REAPED, orphan.call, now - orphan.last_at)
            sealed_now.append(orphan.call)
    for orphan in await queries.unsealed_written(
        serving.connections.pool, now - QUIET_S, limit=AT_MOST
    ):
        # A written call waits for its caller as long as its channel would.
        patience = A_THREAD_WAITS_S if orphan.channel == "whatsapp" else QUIET_S
        if serving.live.calls.get(orphan.call) is not None or now - orphan.last_at < patience:
            continue
        if await _finished(serving, orphan, "timeout"):
            sealed_now.append(orphan.call)
    return sealed_now


# A pass that fails is said, and the next one runs: the reaper never stops.
async def reap_forever(serving: Serving, server: api.LiveKitAPI) -> None:
    """A pass now, and one every minute."""
    while True:
        try:
            await reaped(serving, server, time.time())
        except (Conflict, NotAvailable, api.TwirpError, OSError):
            logger.warning(
                "the reaper's pass failed; the next is in %.0f s", REAPED_EVERY_S, exc_info=True
            )
        await asyncio.sleep(REAPED_EVERY_S)


# The seal never fails on a judge: a call that could not be judged says why and seals all the same.
async def judged_call(
    connections: Connections, entries: Sequence[Entry], declared: AgentConfig | None
) -> CallScore:
    """The hang-up panel over a finished call, a model's judges when the box names one."""
    call = next((entry.call for entry in entries if entry.call is not None), "")
    try:
        configured = await catalog.providers(connections.pool)
        judge, unjudged = await judge_of(connections, configured)
        return await judges.at_hangup(
            entries, declared, judge, configured=configured, unjudged=unjudged
        )
    except PinecallError as broke:
        logger.warning("call %s: nothing judged it", call, exc_info=True)
        return CallScore(judges=[], judge_calls=0, not_judged=JUDGING_BROKE.format(broke=broke))


# Always the box's key, never an org's: judging is the platform's measure, the same for all.
async def judge_of(connections: Connections, configured: Providers) -> tuple[Running | None, str]:
    """The judge model on the box's key, or None and the sentence that says why there is none."""
    if configured.judge is None:
        return None, NO_JUDGE
    if connections.settings.judge_ceiling_usd <= 0:
        return None, NO_CEILING
    box = await vault.box_credentials(connections.pool, connections.vault)
    named = configured.judge.llm
    declared = Model(provider=named.vendor, model=named.model or "")
    return credentials.stage("llm", declared, configured, Keyring(box=box)), ""


def _now(serving: Serving) -> datetime:
    return datetime.fromtimestamp(serving.logs.store.clock(), UTC)


async def _pumped(entries: Subscription, send: Send) -> None:
    try:
        async for entry in entries:
            await send(entry)
    except (OSError, RuntimeError):
        logger.warning("an app socket stopped taking its call's entries", exc_info=True)
        entries.close()


# Duration ends at the last entry, not now: reaping late bills no extra minutes.
async def _finished(serving: Serving, orphan: queries.Unsealed, reason: EndReason) -> bool:
    store = serving.logs.store
    log = serving.logs.writing(orphan.call, orphan.agent)
    written_types = {item.type for item in await store.whole(orphan.call)}
    try:
        if "call.ended" not in written_types:
            ended = CallEnded(
                reason=reason,
                ended_by="platform",
                ended_at=orphan.last_at,
                duration_s=max(orphan.last_at - orphan.started_at, 0.0),
            )
            await log.append("call.ended", ended.written())
        if "call.summary" not in written_types:
            await summed_up(
                serving.connections.pool,
                store,
                log,
                SealCallRequest(usage=[], outcome=NOTHING_SAID),
            )
        score = CallScore(judges=[], judge_calls=0, not_judged=NOT_JUDGED_REAPED)
        await log.append("call.score", score.written())
    except Conflict:
        # Another gateway sealed it first: nothing is left to do.
        return False
    finally:
        serving.logs.forget(orphan.call)
    serving.live.close(orphan.call)
    return True
