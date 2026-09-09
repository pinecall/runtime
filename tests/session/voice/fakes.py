"""What a bridge test runs on: a platform in memory that records, a scripted session, a clinic."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from pinecall.log.entry import ephemeral_by_default
from pinecall.log.reduce import reduce
from pinecall.session.voice.platform import PlatformRefused
from pinecall.types import AgentConfig, CallContext, Route, ToolSpec
from pinecall.types.json import JsonObject
from pinecall_protocol import decode_entry, encode
from pinecall_protocol.defs import ToolResult
from pinecall_protocol.events import ToolCall

CALL = "call_1"
CLINICA = Route(org="pinecall", agent="clinica-norte", channel="web")
BOOK = ToolSpec(
    name="book",
    description="Book the slot",
    parameters={"type": "object", "properties": {"at": {"type": "string"}}},
    side_effect="irreversible",
    confirm="Le reservé el turno de las {{at}}, referencia {{result.ref}}.",
    timeout_s=2.0,
)
FIND = ToolSpec(name="find_slot", description="Free slots", parameters={"type": "object"})
CLARA = AgentConfig(
    slug="clinica-norte",
    channels=frozenset({"web"}),
    instructions="You are Clara.",
    tools=(FIND, BOOK),
)

# What the platform says when no app holds the agent a tool was asked of.
NOBODY_HOLDS_THE_AGENT = "no app is holding agent clinica-norte"


def a_call() -> CallContext:
    """One web call to the clinic, as the worker resolved it."""
    return CallContext(
        call=CALL,
        channel="web",
        direction="inbound",
        caller="visitor_1",
        route=CLINICA,
        today=date(2026, 9, 7),
    )


@dataclass(frozen=True)
class Written:
    """One entry as the session sent it to the platform."""

    type: str
    data: dict[str, Any]
    ephemeral: bool | None


@dataclass(frozen=True)
class Asked:
    """One tool the session ran through the platform: for which call, whose agent, and the use."""

    call: str
    agent: str
    use: ToolCall
    timeout_s: float


# The platform as the session sees it, in memory: every entry appended is kept and numbered the
# way the real one numbers a log — the seqs are what a verdict cites — and every tool the model
# calls is answered from this table, under the call_id the model used.
class Recording:
    """A platform that remembers every entry of the call, and answers tools by name."""

    def __init__(
        self,
        tools: Mapping[str, ToolResult] | None = None,
        refuse: Callable[[str, JsonObject], bool] | None = None,
    ) -> None:
        self._tools = dict(tools or {})
        self._refuse = refuse
        self.entries: list[Written] = []
        self.asked: list[Asked] = []
        self._live: asyncio.Queue[JsonObject] = asyncio.Queue()

    async def append(
        self, call: str, type: str, data: JsonObject, ephemeral: bool | None = None
    ) -> None:
        """Keep the entry, in order; a refusal is the one this platform was told to make."""
        assert call == CALL
        if self._refuse is not None and self._refuse(type, data):
            raise PlatformRefused(f"{type}: refused by the test")
        self.entries.append(Written(type, dict(data), ephemeral))
        self._live.put_nowait(self._as_an_entry(len(self.entries), self.entries[-1]))

    async def tool(self, call: str, agent: str, use: ToolCall, timeout_s: float) -> ToolResult:
        """The table's answer under the model's own call_id; a tool nobody holds is refused."""
        self.asked.append(Asked(call, agent, use, timeout_s))
        answered = self._tools.get(use.name)
        if answered is None:
            raise PlatformRefused(NOBODY_HOLDS_THE_AGENT)
        return answered.model_copy(update={"call_id": use.call_id})

    async def state(self, call: str) -> tuple[JsonObject, int]:  # noqa: ARG002 — the shape
        """The log so far, folded, and the seq it was folded to."""
        entries = [decode_entry(raw) for raw in self._numbered(0)]
        return encode(reduce(entries)), len(self.entries)

    async def since(self, call: str, after: int) -> AsyncIterator[JsonObject]:  # noqa: ARG002
        """Every entry above the cursor, numbered as the real platform numbers them."""
        for raw in self._numbered(after):
            yield raw

    async def tail(self, call: str, after: int) -> AsyncIterator[JsonObject]:
        """The backlog, then whatever is appended after, for as long as the test reads."""
        async for raw in self.since(call, after):
            yield raw
        while True:
            yield await self._live.get()

    @property
    def types(self) -> list[str]:
        """The entry types, in order, for a test that reads a sequence."""
        return [entry.type for entry in self.entries]

    def of(self, type: str) -> list[Written]:
        """Every entry of one type, in order."""
        return [entry for entry in self.entries if entry.type == type]

    def _numbered(self, after: int) -> list[JsonObject]:
        """The durable entries above the cursor, each in the envelope the platform answers in."""
        return [
            self._as_an_entry(seq, one) for seq, one in enumerate(self.entries, 1) if seq > after
        ]

    def _as_an_entry(self, seq: int, written: Written) -> JsonObject:
        forgettable = (
            ephemeral_by_default(written.type) if written.ephemeral is None else written.ephemeral
        )
        return {
            "seq": seq,
            "ts": 0.0,
            "call": CALL,
            "agent": CLARA.slug,
            "type": written.type,
            "ephemeral": forgettable,
            "data": written.data,
        }


# livekit's AgentInput and AgentOutput as the supervise verbs reach them: one switch each, and
# whether it is on. A takeover turns both off, a release turns them back on.
@dataclass
class Audio:
    """One side of the session's audio, as set_audio_enabled leaves it."""

    enabled: bool = True

    def set_audio_enabled(self, enabled: bool) -> None:
        """livekit's own name for the switch (io.py:507 for the input, io.py:630 for the output)."""
        self.enabled = enabled


@dataclass
class ScriptedSession:
    """livekit's session as the bridge subscribes to it: on, off, and the few facts it reads."""

    current_speech: Any = None
    agent_state: str = "listening"
    turn_detection: Any = "manual"
    llm: Any = None
    stt: Any = None
    tts: Any = None
    vad: Any = None
    # Nothing to interrupt: the real session raises then, and a takeover carries on regardless.
    interruptible: bool = True
    said: list[tuple[str, Any]] = field(default_factory=list[tuple[str, Any]])
    replied: list[str] = field(default_factory=list[str])
    interruptions: int = 0
    input: Audio = field(default_factory=Audio)
    output: Audio = field(default_factory=Audio)
    listeners: dict[str, list[Callable[[Any], None]]] = field(
        default_factory=dict[str, list[Callable[[Any], None]]]
    )

    def say(self, text: str, *, allow_interruptions: Any = None) -> None:
        """agent_session.py:1430, as far as a verb reaches it."""
        self.said.append((text, allow_interruptions))

    def generate_reply(self, *, instructions: str) -> None:
        """agent_session.py:1464: one turn now, guided by words the caller never hears."""
        self.replied.append(instructions)

    async def interrupt(self, *, force: bool = False) -> None:  # noqa: ARG002 — force is livekit's
        """agent_session.py:1534, which raises when there is no speech to cut."""
        self.interruptions += 1
        if not self.interruptible:
            raise RuntimeError("AgentSession isn't running")

    def on(self, name: str, callback: Callable[[Any], None]) -> None:
        """Subscribe, as EventEmitter.on does."""
        self.listeners.setdefault(name, []).append(callback)

    def off(self, name: str, callback: Callable[[Any], None]) -> None:
        """Unsubscribe, as EventEmitter.off does."""
        self.listeners.get(name, []).remove(callback)

    def emit(self, name: str, event: Any) -> None:
        """One event to everyone listening for it, synchronously, as livekit emits."""
        for callback in list(self.listeners.get(name, [])):
            callback(event)
