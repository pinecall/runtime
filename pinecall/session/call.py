"""What a call is while it runs, written once for a voice call and a written one."""

import asyncio
import dataclasses
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from livekit.agents.utils import aio

from pinecall.domain.agent import DEFAULT_LAYOUT, AgentConfig, EventSource, PromptBlock, ToolSpec
from pinecall.domain.call import CallContext
from pinecall.domain.errors import DeclarationRefused, NotAvailable
from pinecall.domain.names import JsonObject
from pinecall.session._hearing import policy_for
from pinecall.wire.commands import CallCallback, CallLog, SessionConfigure, StateSet, ToolsSet
from pinecall.wire.events import (
    EPHEMERAL_EVENTS,
    AgentConfigured,
    CallbackRequested,
    Custom,
    EventReceived,
    StateCause,
    StateCauseEvent,
    StateChanged,
    ToolsChanged,
)
from pinecall.wire.frames import Entry, WireModel
from pinecall.wire.metrics import ModelUsage
from pinecall.wire.parts import AgentConfig as Declared
from pinecall.wire.parts import Contact, PlatformTool, Supervisor, ToolResult
from pinecall.wire.rest.calls import BatchedEntry

# What a session reaches outside the process its call runs in. In the gateway these are the
# call's own log and the doors behind it; in a worker, the same doors over the fleet's client.
# `append` is called as `append(kind, data, ephemeral=...)`, as `Log.append` is.
type Append = Callable[..., Awaitable[Entry]]


# Called as `append_many(entries, after=...)`, as `Log.append_many` is: the entries numbered, in
# the order they were sent.
type AppendMany = Callable[..., Awaitable[list[Entry]]]


type Lookup = Callable[[PlatformTool, JsonObject, str | None], Awaitable[JsonObject]]


type Seal = Callable[[list[ModelUsage], str], Awaitable[None]]


logger = logging.getLogger(__name__)


# `sp_<n>`: numbered on from the highest in the log when a call is taken up again.
SPEECH = "sp_"


# A callback the agent took during a call; others come from the widget or an overflow.
BY_THE_AGENT = "agent"


UNDECLARED = "event {name!r} is not one this agent declared the {source} may send"


# A limit under two minutes is told at its half instead.
WARNED_BEFORE_S = 60


CLOSING = (
    "The call reaches its time limit in about a minute. Bring it to a close now: answer what is "
    "pending in a sentence, tell the caller the call has to end soon, and say goodbye."
)


# What one request carries: whatever is queued when the last one was answered, up to this.
MOST_A_BATCH = 64


# Past this many waiting, an ephemeral entry is shed: no store keeps it. A durable one is always
# queued, so a gateway that was away never leaves a hole in the log.
MOST_QUEUED = 4096


SHED = "call {call}: {queued} entries wait for the log, so {kind} was shed"


@dataclass(frozen=True)
class ToolUse:
    """One tool call the model made: its id, the tool, and the arguments it chose."""

    call_id: str
    name: str
    arguments: JsonObject


type RunTool = Callable[[ToolUse, str | None], Awaitable[ToolResult]]


class Writing:
    """The call's entries in the order they happened, sent to the log in batches."""

    # livekit calls most listeners synchronously; one queue drained by one task keeps order. A
    # writer that takes over a call another wrote to follows on from what the log took from it.
    def __init__(self, append_many: AppendMany, call: str, *, after: int = 0) -> None:
        """An empty queue for the call, drained once `open` starts it."""
        self.append_many = append_many
        self.call = call
        self.queued: asyncio.Queue[tuple[BatchedEntry, asyncio.Future[Entry]]] = asyncio.Queue()
        self.draining: asyncio.Task[None] | None = None
        # How many entries the log took from this call's writer: what the next batch follows.
        self.after = after
        self.refused: list[str] = []
        self.shed: list[str] = []

    def open(self) -> None:
        """Start sending."""
        if self.draining is None:
            self.draining = asyncio.create_task(self._drain())

    # livekit calls most listeners synchronously: they queue and move on; a caller that needs
    # the entry (its seq) awaits the future. The stamp is when it happened, not when it was sent.
    def write(
        self, kind: str, event: WireModel, *, ephemeral: bool | None = None
    ) -> asyncio.Future[Entry]:
        """Queue an entry; the future is the entry once the log holds it."""
        written: asyncio.Future[Entry] = asyncio.get_running_loop().create_future()
        forgettable = kind in EPHEMERAL_EVENTS if ephemeral is None else ephemeral
        waiting = self.queued.qsize()
        if forgettable and waiting >= MOST_QUEUED:
            self.shed.append(kind)
            _refused(written, NotAvailable(SHED.format(call=self.call, queued=waiting, kind=kind)))
            return written
        entry = BatchedEntry(type=kind, data=event.written(), ephemeral=ephemeral, ts=time.time())
        self.queued.put_nowait((entry, written))
        return written

    async def flushed(self, within_s: float) -> None:
        """Wait until every queued entry was sent; TimeoutError past `within_s`."""
        await asyncio.wait_for(self.queued.join(), within_s)

    async def close(self, within_s: float) -> None:
        """Send what is queued for up to `within_s`, then stop and say what never went."""
        try:
            await self.flushed(within_s)
        except TimeoutError:
            logger.warning(
                "call %s: %d entries never reached the log within %gs",
                self.call,
                self.queued.qsize(),
                within_s,
            )
        if self.draining is not None:
            await aio.cancel_and_wait(self.draining)
            self.draining = None
        if self.refused:
            logger.warning(
                "call %s: the log refused %d entries (%s)",
                self.call,
                len(self.refused),
                ", ".join(sorted(set(self.refused))),
            )
        if self.shed:
            logger.warning(
                "call %s: %d ephemeral entries were shed at the queue's ceiling (%s)",
                self.call,
                len(self.shed),
                ", ".join(sorted(set(self.shed))),
            )

    # No timer: while one batch is out the next one fills, so an idle call sends each entry
    # alone and a busy one batches itself.
    async def _drain(self) -> None:
        while True:
            batch = [await self.queued.get()]
            while len(batch) < MOST_A_BATCH and not self.queued.empty():
                batch.append(self.queued.get_nowait())
            try:
                await self._sent(batch)
            finally:
                for _ in batch:
                    self.queued.task_done()

    # A refused batch never ends the call: the client retries what is transient with the same
    # `after`, so what fails here is a refusal of the whole batch, counted and named at close.
    async def _sent(self, batch: list[tuple[BatchedEntry, asyncio.Future[Entry]]]) -> None:
        kinds = [entry.type for entry, _ in batch]
        try:
            numbered = await self.append_many([entry for entry, _ in batch], after=self.after)
        except asyncio.CancelledError:
            self.refused += kinds
            raise
        except Exception as refused:
            logger.warning(
                "call %s: the log refused a batch of %d", self.call, len(batch), exc_info=True
            )
            self.refused += kinds
            for _, written in batch:
                if not written.done():
                    _refused(written, refused)
            return
        self.after += len(batch)
        for (_, written), entry in zip(batch, numbered, strict=True):
            if not written.done():
                written.set_result(entry)


@dataclass(frozen=True)
class Platform:
    """The log a call writes, the app's tools, the lookups, and the seal at the end."""

    append_many: AppendMany
    tool: RunTool
    lookup: Lookup
    seal: Seal


class Call:
    """One call's state that outlives a turn, and the entries the app's commands write."""

    def __init__(
        self,
        context: CallContext,
        config: AgentConfig,
        platform: Platform,
        recording: Path | None = None,
    ) -> None:
        """A call nobody has spoken on yet, every declared tool open."""
        self.context = context
        self.config = config
        # Where its session records its audio: none for a written call or one the agent keeps none.
        self.recording = recording
        self.turn_policy = policy_for(config.language)
        self.platform = platform
        self.writing = Writing(platform.append_many, context.call)
        self.speeches = 0
        self.turns = 0
        self.last_said = ""
        # What the next state.set is answering: a running tool, or an event the app received.
        self.cause: StateCause | None = None
        self.taken_by: Supervisor | None = None
        self.waiting_for_a_person = False
        # As the session's agent_state events say, not as livekit's internals hold it.
        self.agent_speaking = False
        self.open_tools = frozenset(tool.name for tool in config.tools)
        # Every request the model was sent, kept on a call an eval run opened and on no other.
        self.requests: list[JsonObject] = []

    def speech(self) -> str:
        """A new speech id, `sp_<n>`."""
        self.speeches += 1
        return f"{SPEECH}{self.speeches}"

    @property
    def a_person_has_the_line(self) -> bool:
        """Whether the model must not answer: a supervisor holds the line, or one is awaited."""
        return self.taken_by is not None or self.waiting_for_a_person

    async def set_state(self, wanted: StateSet) -> None:
        """The app's whole state, and what it answers when a tool or an event caused it."""
        changed = wanted.changed if wanted.changed is not None else sorted(wanted.state)
        cause, self.cause = self.cause, None
        written = StateChanged(state=wanted.state, changed=changed)
        if cause is not None:
            written.cause = cause
        await self.writing.write("state.changed", written)

    async def receives(
        self, name: str, data: JsonObject, *, source: EventSource, identity: str | None = None
    ) -> None:
        """An event the agent declared this sender may send, the cause of the next state.set."""
        if not self.config.accepts(name, source):
            raise DeclarationRefused(UNDECLARED.format(name=name, source=source))
        received = EventReceived(name=name, data=data, source=source, identity=identity)
        entry = await self.writing.write("event.received", received)
        self.cause = StateCauseEvent(kind="event", name=name, seq=entry.seq)

    async def log_custom(self, wanted: CallLog) -> None:
        """A line of the app's own, as it sent it."""
        await self.writing.write("custom", Custom(name=wanted.name, data=wanted.data))

    async def call_back(self, wanted: CallCallback) -> None:
        """A callback the agent took, in the call's log, where the callbacks door collects it."""
        contact = self.context.contact
        await self.writing.write(
            "callback.requested",
            CallbackRequested(
                channel=self.context.channel,
                number=wanted.number,
                via=BY_THE_AGENT,
                call=self.context.call,
                contact=None if contact is None else Contact.model_validate(asdict(contact)),
                when=wanted.when,
                note=wanted.note,
            ),
        )

    # What the app sets up before the first turn; a declaration sent here lasts this call only.
    async def configure(self, wanted: SessionConfigure) -> None:
        """The app's declaration and state for this call."""
        if wanted.config is not None:
            self.config = with_app_fields(self.config, wanted.config)
            configured = AgentConfigured(changed=changed_by(wanted.config))
            await self.writing.write("agent.configured", configured)
        if wanted.state is not None:
            await self.set_state(StateSet(state=wanted.state))

    # Every tool stays declared for the whole call: changing what the provider is sent spends
    # its prompt cache. The subset open now is enforced where a tool is run.
    async def set_tools(self, wanted: ToolsSet) -> None:
        """Open only these of the declared tools; an empty list closes them all."""
        declared = {tool.name for tool in self.config.tools}
        self.open_tools = frozenset(tool.name for tool in wanted.tools if tool.name in declared)
        await self.writing.write("tools.changed", ToolsChanged(visible=sorted(self.open_tools)))


# A declaration is a patch: only the fields sent change. What the org sets per world (the
# voice, the models, memory, the greeting) comes from its settings and is not taken from here.
def with_app_fields(current: AgentConfig, declared: Declared) -> AgentConfig:
    """The agent's declaration with the fields the app sent in place."""
    sent = declared.model_fields_set
    changed: dict[str, object] = {}
    if "prompt" in sent:
        changed["prompt"] = (
            tuple(PromptBlock(block.name, block.region) for block in declared.prompt)
            if declared.prompt
            else DEFAULT_LAYOUT
        )
    if "language" in sent:
        changed["language"] = declared.language
    if "uses_knowledge" in sent:
        changed["uses_knowledge"] = declared.uses_knowledge
    if "tools" in sent:
        changed["tools"] = tuple(
            ToolSpec(
                name=tool.name,
                description=tool.description,
                parameters=tool.parameters,
                side_effect=tool.side_effect,
                pii=frozenset(tool.pii or ()),
                confirm=tool.confirm,
                announce=tool.announce,
                timeout_s=ToolSpec.timeout_s if tool.timeout_s is None else tool.timeout_s,
            )
            for tool in declared.tools or ()
        )
    if "state_fields" in sent:
        changed["state_fields"] = {
            item.name: item.visibility for item in declared.state_fields or ()
        }
    if "view" in sent:
        changed["view"] = None if declared.view is None else declared.view.name
    if "events" in sent:
        changed["events"] = {item.name: frozenset(item.from_) for item in declared.events or ()}
    return dataclasses.replace(current, **changed)


def changed_by(declared: Declared) -> list[str]:
    """The fields a declaration sets, sorted."""
    return sorted(declared.model_fields_set)


# Most entries are queued and never awaited: read here, the refusal is not logged a second time
# by asyncio as an exception nobody retrieved. `close` names what was refused and what was shed.
def _refused(written: asyncio.Future[Entry], why: Exception) -> None:
    written.set_exception(why)
    written.exception()
