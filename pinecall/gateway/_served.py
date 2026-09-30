"""The calls this gateway serves: opened, handed to an app, parked, claimed, and looked up for."""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Literal

from pinecall.domain.agent import AgentConfig
from pinecall.domain.call import CallContext
from pinecall.domain.names import CHANNELS_WITH_A_NUMBER, Env, JsonObject
from pinecall.domain.scope import Scope
from pinecall.gateway._sockets import Process, Registration, SocketId, Sockets, orgs_own
from pinecall.log.logs import Log, Logs, Subscription, arrival_entry
from pinecall.log.reduce import reduce
from pinecall.log.store import Store
from pinecall.process.connections import Connections
from pinecall.retrieval import lookups
from pinecall.retrieval.embed import Embedder
from pinecall.retrieval.lookups import OnTheCall
from pinecall.session.session import Session
from pinecall.session.tools import ToolCalls
from pinecall.tenancy import admission
from pinecall.tenancy.codes import Codes
from pinecall.tenancy.prompts import Prompts
from pinecall.wire.commands import DevAnswer
from pinecall.wire.events import (
    CallAttached,
    CallClaimed,
    CallStarted,
)
from pinecall.wire.frames import Command, Entry
from pinecall.wire.rest.calls import LookupRequest

type Send = Callable[[Entry], Awaitable[None]]


logger = logging.getLogger(__name__)


# Entries a taking-over socket is rebuilt from; the prompt is not, the log keeps its hash alone.
STARTED = "call.started"


CLAIMED = "call.claimed"


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
    # Held while the call is sealed, so its seal runs once however many times it is asked for.
    sealing: asyncio.Lock = field(default_factory=asyncio.Lock)

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
    # for ever. Per world, as every quota is: the sandbox's calls never close production.
    def running(self, org: str, env: Env) -> int:
        """How many of the org's calls are open here in the world."""
        return sum(
            1
            for served in self.calls.values()
            if served.scope.org == org and served.scope.env == env
        )

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
    # The blocks of prompt this process has kept, so a call's prompt is read back whole.
    prompts: Prompts = field(default_factory=Prompts)


# A call opened with a key goes to that key's scope; one that rang, to its phone owner, or the line.
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
        now=now_of(serving),
    )
    return await lookups.lookup(pool, serving.embedder, on_the_call, request, quotas=quotas)


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
) -> None:
    """Give every parked call of the agent in the scope to this socket."""
    for call in live.parked(scope, slug):
        await attach(live, store, call, app)


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


def now_of(serving: Serving) -> datetime:
    """This moment by the store's clock, the one every entry of a call is stamped with."""
    return datetime.fromtimestamp(serving.logs.store.clock(), UTC)


async def _pumped(entries: Subscription, send: Send) -> None:
    try:
        async for entry in entries:
            await send(entry)
    except (OSError, RuntimeError):
        logger.warning("an app socket stopped taking its call's entries", exc_info=True)
        entries.close()
