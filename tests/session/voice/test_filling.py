"""The markers on a spoken call: filled when the caller's turn ends, remembered at hang-up."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import replace

import pytest
from livekit.agents import llm as agents
from livekit.agents.voice import AgentSession

from pinecall._settings import Budgets
from pinecall.log import hashed_prompt
from pinecall.session.voice import VoiceBridge, a_bridge
from pinecall.types import KnowledgeFile, Marker, MemoryPolicy
from tests.session.fake_llm import FakeLLM, Scripted
from tests.session.voice.fakes import CALL, CLARA, Recording
from tests.session.voice.fakes import a_call as a_context

pytestmark = pytest.mark.unit

A_FILE = KnowledgeFile("./knowledge/clinica.md", "The clinic opens at nine and closes at six.")
KNOWLEDGE = "<!-- knowledge: ./knowledge/clinica.md -->"
MEMORY = '<!-- memory: {"kinds":["preference"],"limit":6} -->'
A_VIEW = f"The caller is Ana.\n\n## You remember\n\n{MEMORY}"

REMEMBERS = replace(CLARA, knowledge=A_FILE, memory=MemoryPolicy(remember=("preference",)))

type Talking = tuple[Recording, VoiceBridge, AgentSession[None], FakeLLM]


class Answering:
    """A filler that answers memory with what it was asked, and a rememberer that may fail."""

    def __init__(self, failing: Exception | None = None) -> None:
        self._failing = failing
        self.queries: list[tuple[str, str | None]] = []
        self.remembered: list[str] = []

    async def fill(
        self, call: str, query: str, markers: Sequence[Marker], speech_id: str | None
    ) -> Mapping[str, str]:
        assert call == CALL
        self.queries.append((query, speech_id))
        return {marker.line: f"- The caller said {query!r}." for marker in markers}

    async def remember(self, call: str) -> int:
        if self._failing is not None:
            raise self._failing
        self.remembered.append(call)
        return 1


@pytest.fixture
async def answering() -> Answering:
    return Answering()


@pytest.fixture
async def talking(answering: Answering) -> AsyncIterator[Talking]:
    """A headless session on the scripted model, the bridge filled by `answering`."""
    recording = Recording()
    llm = FakeLLM(Scripted(chunks=("Uno.",)), Scripted(chunks=("Dos.",)))
    bridge = a_bridge(a_context(), REMEMBERS, recording, filler=answering, rememberer=answering)
    live: AgentSession[None] = AgentSession(
        llm=llm, vad=None, turn_handling={"turn_detection": "manual"}
    )
    await bridge.opened(live)
    await live.start(bridge.agent, record=False)  # pyright: ignore[reportUnknownMemberType]
    yield recording, bridge, live, llm
    await live.aclose()


async def test_the_callers_turn_is_the_query_and_its_fill_closes_the_next_request(
    talking: Talking, answering: Answering
) -> None:
    _recording, bridge, live, llm = talking
    await bridge.set_prompt("identity", "You are Clara.")
    await bridge.set_prompt("view", A_VIEW)
    await _the_caller_said(bridge, "quiero un turno")
    await live.generate_reply(user_input="quiero un turno")
    assert answering.queries == [("quiero un turno", None)]
    (asked,) = llm.asked
    assert asked.system.endswith("## You remember\n\n- The caller said 'quiero un turno'.")
    assert MEMORY not in asked.system


async def test_the_knowledge_file_is_in_the_static_prefix_and_the_hash_is_of_the_apps_text(
    talking: Talking,
) -> None:
    recording, bridge, live, llm = talking
    written = f"## What you know\n\n{KNOWLEDGE}"
    await bridge.set_prompt("knowledge", written)
    await live.generate_reply(user_input="hola")
    await _the_caller_said(bridge, "¿a qué hora abren?")
    await live.generate_reply(user_input="¿a qué hora abren?")
    first, second = llm.asked
    assert first.instructions == second.instructions == f"## What you know\n\n{A_FILE.text}"
    assert bridge.agent.instructions == written
    (changed,) = recording.of("prompt.changed")
    assert changed.data["hash"] == hashed_prompt(written)


async def test_a_fill_past_its_budget_is_a_recoverable_entry_and_the_turn_goes_on() -> None:
    recording = Recording()
    bridge = a_bridge(
        a_context(),
        REMEMBERS,
        recording,
        filler=Answering(),
        budgets=Budgets(fill_ms=0, remember_s=1.0),
    )
    live: AgentSession[None] = AgentSession(
        llm=FakeLLM(Scripted(chunks=("Uno.",))),
        vad=None,
        turn_handling={"turn_detection": "manual"},
    )
    await bridge.opened(live)
    await live.start(bridge.agent, record=False)  # pyright: ignore[reportUnknownMemberType]
    await bridge.set_prompt("view", A_VIEW)
    await _the_caller_said(bridge, "hola")
    await live.generate_reply(user_input="hola")
    await bridge.closed("the test hung up")
    (skipped,) = recording.of("error")
    assert skipped.data["code"] == "memory_skipped"
    assert skipped.data["recoverable"] is True
    assert "turn.agent" in recording.types


async def test_hang_up_remembers_between_call_ended_and_call_summary(
    talking: Talking, answering: Answering
) -> None:
    recording, bridge, live, _llm = talking
    await live.generate_reply(user_input="hola")
    await bridge.closed("the test hung up")
    assert answering.remembered == [CALL]
    assert recording.types[-3:] == ["call.ended", "call.summary", "call.score"]


async def test_a_rememberer_that_fails_is_an_entry_and_the_call_still_seals() -> None:
    recording = Recording()
    failing = Answering(failing=RuntimeError("no model at hang-up"))
    bridge = a_bridge(a_context(), REMEMBERS, recording, rememberer=failing)
    live: AgentSession[None] = AgentSession(
        llm=FakeLLM(), vad=None, turn_handling={"turn_detection": "manual"}
    )
    await bridge.opened(live)
    await live.start(bridge.agent, record=False)  # pyright: ignore[reportUnknownMemberType]
    await bridge.closed("the test hung up")
    ended = recording.types.index("call.ended")
    assert recording.types[ended:] == ["call.ended", "error", "call.summary", "call.score"]
    (failed,) = recording.of("error")
    assert failed.data == {
        "code": "remember_failed",
        "message": "memory was not written: no model at hang-up",
        "recoverable": True,
    }


async def test_an_agent_that_declared_no_memory_asks_nobody_at_hang_up() -> None:
    recording = Recording()
    answering = Answering()
    bridge = a_bridge(a_context(), CLARA, recording, rememberer=answering)
    live: AgentSession[None] = AgentSession(
        llm=FakeLLM(), vad=None, turn_handling={"turn_detection": "manual"}
    )
    await bridge.opened(live)
    await live.start(bridge.agent, record=False)  # pyright: ignore[reportUnknownMemberType]
    await bridge.closed("the test hung up")
    assert answering.remembered == []


# livekit runs the hook off its audio recognition, which a headless session has none of; the
# test runs it as livekit does, with the message the turn becomes (agent_activity.py:2605).
async def _the_caller_said(bridge: VoiceBridge, text: str) -> None:
    message = agents.ChatMessage(role="user", content=[text])
    await bridge.agent.on_user_turn_completed(bridge.agent.chat_ctx, message)
