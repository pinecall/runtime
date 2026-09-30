"""The calls this gateway serves: opened, handed to an app, parked, claimed, and looked up for."""

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from functools import partial
from typing import Literal

from pinecall.domain.agent import AgentConfig
from pinecall.domain.call import CallContext
from pinecall.domain.errors import NotAvailable
from pinecall.domain.names import CHANNELS_WITH_A_NUMBER, Env, JsonObject
from pinecall.domain.scope import Scope
from pinecall.gateway._sockets import Process, Registration, SocketId, Sockets, orgs_own
from pinecall.gateway.calls.commands import COMMANDS_CHANNEL, SUPERVISOR_VERB
from pinecall.gateway.calls.owners import Owners
from pinecall.gateway.calls.pump import Bound, Send, pumped, told_bound
from pinecall.log import queries
from pinecall.log.logs import Log, Logs, arrival_entry
from pinecall.log.private import Privacy
from pinecall.process.connections import Connections
from pinecall.process.metrics import Counters
from pinecall.process.signal import LocalSignal, Signal
from pinecall.retrieval import lookups
from pinecall.retrieval.embed import Embedder
from pinecall.retrieval.lookups import OnTheCall
from pinecall.session.call import with_app_fields
from pinecall.session.session import Session
from pinecall.session.tools import ToolCalls
from pinecall.tenancy import admission
from pinecall.tenancy.codes import Codes
from pinecall.tenancy.prompts import Prompts
from pinecall.wire.commands import DevAnswer, SessionConfigure, SupervisorVerb, command_of
from pinecall.wire.events import (
    CallClaimed,
)
from pinecall.wire.frames import Command, Entry, WireModel
from pinecall.wire.rest.calls import LookupRequest

logger = logging.getLogger(__name__)


# Entries a taking-over socket is rebuilt from; the prompt is not, the log keeps its hash alone.
STARTED = "call.started"

# How long a call another gateway opened stays served here with no door asking for it.
FIRST_SEEN_IDLE_S = 600.0


CLAIMED = "call.claimed"


# A call belongs to its agent, not to a socket: `app` is None while it waits for one.
@dataclass(frozen=True)
class Served:
    """A call this gateway serves: the socket it reaches, its log, its commands, its tools."""

    agent: str
    scope: Scope
    app: SocketId | None
    log: Log
    # A None ends the worker's command stream.
    commands: asyncio.Queue[Command | None]
    context: CallContext
    config: AgentConfig
    tools: ToolCalls
    # A written call runs here; a voice call runs in a worker.
    session: Session | None = None
    # Held while the call is sealed, so its seal runs once however many times it is asked for.
    sealing: asyncio.Lock = field(default_factory=asyncio.Lock)
    # False for a call another gateway opened, served here from what was kept when it opened:
    # it counts on its opener's gateway, and no socket here takes it as parked.
    opened_here: bool = True

    @property
    def call(self) -> str:
        """The call's id."""
        return self.context.call


# Not durable on purpose: it says which sockets are open now; the log is the record.
class ServedCalls:
    """The open app sockets and the calls served to them."""

    def __init__(self, signal: Signal | None = None) -> None:
        """Nothing open; bindings to other gateways' sockets told on the signal."""
        self.signal = signal or LocalSignal()
        # Which written calls and threads run here, and on the other gateways.
        self.owners = Owners(self.signal)
        self.sockets: dict[SocketId, Send] = {}
        self.processes: dict[SocketId, Process] = {}
        self.calls: dict[str, Served] = {}
        # The calls first seen here, by when a door last asked for one: let go once idle.
        self.seen: dict[str, float] = {}
        self.pending_answers: dict[str, asyncio.Future[DevAnswer]] = {}
        # Each bound call's pump to its socket, and each call's listener for its commands, by call:
        # held here, since asyncio keeps weak references to tasks, and cancelled when it ends.
        self.pumps: dict[str, asyncio.Task[None]] = {}
        self.listening: dict[str, tuple[asyncio.Event, asyncio.Task[None]]] = {}

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
        self.pumped(served.call, after=0)
        heard = asyncio.Event()
        self.listening[served.call] = (
            heard,
            asyncio.create_task(self._commands(served.call, heard)),
        )

    async def commands_heard(self, call: str) -> None:
        """Return once the call's commands are listened for here."""
        listening = self.listening.get(call)
        if listening is not None:
            await listening[0].wait()

    # Synchronous, so two sockets taking one parked call cannot both have it. The new socket's pump
    # starts once call.attached is written (`attach`), from it.
    def attach(self, call: str, app: SocketId | None) -> Served | None:
        """Move the call to this socket (None parks it); None when nothing moved."""
        served = self.calls.get(call)
        if served is None or served.app == app:
            return None
        self._stopped(call)
        moved = replace(served, app=app)
        self.calls[call] = moved
        return moved

    # What any gateway writes to the call reaches its socket here, in seq order, from `after`;
    # `then` goes down right after the first entry (the tools still waiting, after call.attached).
    def pumped(self, call: str, *, after: int, then: Sequence[Entry] = ()) -> None:
        """Start sending the call's entries to its socket, if the socket is open here."""
        served = self.calls.get(call)
        if served is None or served.app is None:
            return
        send = self.sockets.get(served.app)
        if send is None:
            told_bound(self.signal, served.app, Bound(call=call, after=after))
            return
        self._stopped(call)
        self.pumps[call] = asyncio.ensure_future(pumped(served.log, after, send, then))

    def bound_to(self, app: SocketId) -> list[str]:
        """The calls this socket serves."""
        return [call for call, served in self.calls.items() if served.app == app]

    def parked(self, scope: Scope, agent: str) -> list[str]:
        """The calls of the agent in the scope that wait for a socket."""
        return [
            call
            for call, served in self.calls.items()
            if served.app is None
            and served.opened_here
            and served.agent == agent
            and served.scope == scope
        ]

    # The concurrent calls quota counts these, never head rows: a dead worker's row would count
    # for ever. Per world, as every quota is: the sandbox's calls never close production.
    def running(self, org: str, env: Env) -> int:
        """How many of the org's calls are open here in the world."""
        return sum(
            1
            for served in self.calls.values()
            if served.opened_here and served.scope.org == org and served.scope.env == env
        )

    def in_use(self, call: str, now: float) -> None:
        """A door asked for a call first seen here: it stays while it is asked for."""
        if call in self.seen:
            self.seen[call] = now

    # A call first seen here is a cache of what was kept: let go when idle, and read again later.
    def idle(self, now: float) -> None:
        """Let go of the calls first seen here that no door asked for in FIRST_SEEN_IDLE_S."""
        idle = [call for call, at in self.seen.items() if now - at > FIRST_SEEN_IDLE_S]
        for call in idle:
            if call not in self.pumps:
                self.close(call)

    def close(self, call: str) -> None:
        """Forget a finished call: its entries end, and its worker's command stream."""
        self.seen.pop(call, None)
        served = self.calls.pop(call, None)
        if served is None:
            return
        self._stopped(call)
        served.commands.put_nowait(None)
        self.owners.running(call, here=False)
        listening = self.listening.pop(call, None)
        if listening is not None:
            listening[1].cancel()

    # A voice call's commands wait here for its worker's stream; a written call's session takes
    # them at once. Heard whichever gateway the app or the desk sent them to.
    async def _commands(self, call: str, heard: asyncio.Event) -> None:
        try:
            listening = await self.signal.subscribe(COMMANDS_CHANNEL.format(call=call))
        except NotAvailable:
            logger.warning("call %s: its commands are not heard here, the signal is down", call)
            return
        finally:
            heard.set()
        try:
            async for data in listening:
                await self._applied(call, Command.model_validate_json(data))
        finally:
            listening.close()

    async def _applied(self, call: str, command: Command) -> None:
        served = self.calls.get(call)
        if served is None or served.agent != command.agent:
            return
        if served.session is None:
            served.commands.put_nowait(command)
        elif command.type == SUPERVISOR_VERB:
            await served.session.supervise(SupervisorVerb.model_validate(command.data))
        else:
            model = command_of(command)
            declared_for_the_call(served, model)
            await served.session.apply(model)

    def _stopped(self, call: str) -> None:
        pump = self.pumps.pop(call, None)
        if pump is not None:
            pump.cancel()


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
    # What the process counts, for /metrics: the seal holds up an org's unusual spend there.
    counters: Counters = field(default_factory=Counters)


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
    log.privacy = Privacy(serving.connections.vault, config)
    served = Served(
        agent=config.slug,
        scope=scope,
        app=owner,
        log=log,
        commands=asyncio.Queue(),
        context=context,
        config=config,
        tools=ToolCalls(
            config,
            log,
            partial(queries.tool_answered, serving.connections.pool, context.call),
            partial(queries.tool_called, serving.connections.pool, context.call),
        ),
    )
    serving.live.serve(served)
    return serving.live.calls[context.call]


# Parked and not counted: the socket holding its agent and the quota are its opener's.
def first_seen(
    serving: Serving, context: CallContext, config: AgentConfig, scope: Scope, now: float
) -> Served:
    """Serve a call another gateway opened, from what was kept when it opened."""
    serving.live.idle(now)
    served = served_call(serving, None, context, config, scope)
    served = replace(served, opened_here=False)
    serving.live.calls[served.call] = served
    serving.live.seen[served.call] = now
    return served


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


def declared_for_the_call(served: Served, model: WireModel) -> None:
    """A written call's own declaration of what is private, as the app configured it."""
    privacy = served.log.privacy
    if isinstance(model, SessionConfigure) and model.config is not None and privacy is not None:
        declared = with_app_fields(privacy.config, model.config)
        served.log.privacy = replace(privacy, config=declared)
