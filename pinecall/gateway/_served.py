"""The calls this gateway serves: handed to an app socket, parked, told their commands, counted."""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field, replace

from pydantic import TypeAdapter

from pinecall.domain.agent import AgentConfig
from pinecall.domain.call import CallContext
from pinecall.domain.errors import NotAvailable
from pinecall.domain.names import Env
from pinecall.domain.scope import Scope
from pinecall.gateway._sockets import Process, SocketId
from pinecall.gateway.calls.commands import COMMANDS_CHANNEL, SUPERVISOR_VERB
from pinecall.gateway.calls.inbox import (
    ANSWER_CHANNEL,
    Bound,
    Elsewhere,
    ForApp,
    SocketRow,
    answered_back,
    told_app,
    told_bound,
)
from pinecall.gateway.calls.owners import Owners
from pinecall.gateway.calls.pump import Send, pumped
from pinecall.log.logs import Log, Logs
from pinecall.process.connections import Connections
from pinecall.process.metrics import Counters
from pinecall.process.shared import Shared
from pinecall.process.signal import LocalSignal, Signal
from pinecall.retrieval.embed import Embedder
from pinecall.session.call import with_app_fields
from pinecall.session.session import Session
from pinecall.session.tools import ToolCalls
from pinecall.tenancy.prompts import Prompts
from pinecall.wire.commands import DevAnswer, SessionConfigure, SupervisorVerb, command_of
from pinecall.wire.frames import Command, Entry, WireModel

logger = logging.getLogger(__name__)


# Entries a taking-over socket is rebuilt from; the prompt is not, the log keeps its hash alone.
STARTED = "call.started"

# Where each gateway says how many calls each org runs on it, and how often.
RUNNING_CHANNEL = "running"
RUNNING_EVERY_S = 1.0
_RUNNING: TypeAdapter[dict[str, int]] = TypeAdapter(dict[str, int])

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
        # Which written calls and threads run here, and on the other gateways; their sockets.
        self.owners = Owners(self.signal)
        self.elsewhere = Elsewhere(self.signal)
        # Each org's calls at once in each world, as every gateway counts its own each second.
        self.counted = Shared(self.signal, RUNNING_CHANNEL, _RUNNING, {}, lambda: None)
        self.counted.every_s = RUNNING_EVERY_S
        self.counted.gathered = self._running_here
        # Each dev.request asked here, listening for its answer from the socket's gateway.
        self.answers: dict[str, asyncio.Task[None]] = {}
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

    async def listen(self) -> None:
        """Hear the other gateways' owners and sockets, and say this one's."""
        await self.owners.start()
        await self.elsewhere.start()
        await self.counted.start()

    async def quiet(self) -> None:
        """Stop hearing and saying."""
        await self.owners.close()
        await self.elsewhere.close()
        await self.counted.close()

    def connect(self, process: Process, send: Send) -> None:
        """An app socket opened."""
        self.sockets[process.app] = send
        self.processes[process.app] = process
        self._say_sockets()

    def disconnect(self, app: SocketId) -> None:
        """An app socket closed; its calls go on until another socket takes them."""
        self.sockets.pop(app, None)
        self.processes.pop(app, None)
        self._say_sockets()

    def named(self, app: SocketId, host: str | None) -> None:
        """The machine the app said it runs on."""
        process = self.processes.get(app)
        if process is not None and host is not None:
            self.processes[app] = replace(process, host=host)
            self._say_sockets()

    # The other gateways' sockets are stopped through their inbox, as any message to them is.
    def processes_of(self, org: str, env: Env) -> list[Process]:
        """The org's sockets in the world on every gateway, oldest first."""
        mine = [
            value
            for value in self.processes.values()
            if value.scope.org == org and value.scope.env == env
        ]
        theirs = [
            Process(
                row.app, row.scope, row.address, row.connected_at, self._stop(row.app), row.host
            )
            for row in self.elsewhere.rows_of(org, env)
        ]
        return sorted([*mine, *theirs], key=lambda one: one.connected_at)

    # dev.request is never stored: it goes straight down the socket, wherever it is held.
    async def tell(self, app: SocketId, entry: Entry) -> bool:
        """Send an entry to one socket; False when no gateway holds it."""
        send = self.sockets.get(app)
        if send is None:
            return await told_app(self.signal, app, ForApp(entry=entry))
        await send(entry)
        return True

    async def ask(self, params: str) -> asyncio.Future[DevAnswer]:
        """A future for the answer to a dev.request, answered here or by the socket's gateway."""
        answer: asyncio.Future[DevAnswer] = asyncio.get_running_loop().create_future()
        self.pending_answers[params] = answer
        heard = asyncio.Event()
        task = asyncio.create_task(self._answer_from_there(params, answer, heard))
        self.answers[params] = task

        # Answered, or given up on (the asker cancels it): the listener goes with it.
        def over(_: asyncio.Future[DevAnswer]) -> None:
            self.answers.pop(params, None)
            task.cancel()

        answer.add_done_callback(over)
        await heard.wait()
        return answer

    async def dev_answered(self, answer: DevAnswer) -> bool:
        """Hand an app's dev.answer to the door waiting for it; False when nobody waits."""
        waiting = self.pending_answers.pop(answer.id, None)
        if waiting is None:
            return await answered_back(self.signal, answer)
        if waiting.done():
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
    # The concurrent-calls quota reads it: every gateway's count, the others' a second old at most,
    # so two gateways admitting at once may go past the limit by what they opened that second.
    def running(self, org: str, env: Env) -> int:
        """How many of the org's calls are open in the world, on every gateway of the box."""
        named = f"{org}|{env}"
        theirs = sum(heard.share.get(named, 0) for heard in self.counted.theirs.values())
        return self._running_here().get(named, 0) + theirs

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

    def _running_here(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for served in self.calls.values():
            if served.opened_here:
                named = f"{served.scope.org}|{served.scope.env}"
                counts[named] = counts.get(named, 0) + 1
        return counts

    def _say_sockets(self) -> None:
        self.elsewhere.shared.put(
            tuple(
                SocketRow(item.app, item.scope, item.address, item.connected_at, item.host)
                for item in self.processes.values()
            )
        )

    def _stop(self, app: SocketId) -> Callable[[str], Awaitable[None]]:
        async def stop(why: str) -> None:
            await told_app(self.signal, app, ForApp(stop=why))

        return stop

    async def _answer_from_there(
        self, params: str, answer: asyncio.Future[DevAnswer], heard: asyncio.Event
    ) -> None:
        try:
            listening = await self.signal.subscribe(ANSWER_CHANNEL.format(id=params))
        except NotAvailable:
            return
        finally:
            heard.set()
        try:
            async for data in listening:
                if not answer.done():
                    answer.set_result(DevAnswer.model_validate_json(data))
                return
        finally:
            listening.close()

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


def declared_for_the_call(served: Served, model: WireModel) -> None:
    """A written call's own declaration of what is private, as the app configured it."""
    privacy = served.log.privacy
    if isinstance(model, SessionConfigure) and model.config is not None and privacy is not None:
        declared = with_app_fields(privacy.config, model.config)
        served.log.privacy = replace(privacy, config=declared)
