"""The lookups of a spoken call: run when the caller's turn ends, remembered at hang-up."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from dataclasses import replace
from typing import Any, override

import pytest
from livekit.agents import llm as agents
from livekit.agents.voice import AgentSession
from livekit.agents.voice import events as session_events

from pinecall.session.voice import VoiceBridge, build_bridge
from pinecall.settings import Budgets
from pinecall.types import Docs, MemoryPolicy, PlatformTool
from tests.session.fake_llm import FakeLLM, Scripted
from tests.session.voice.fakes import CALL, CLARA, Recording
from tests.session.voice.fakes import a_call as a_context

pytestmark = pytest.mark.unit

A_VIEW = "The caller is Ana. Two slots are free."

REMEMBERS = replace(
    CLARA, memory=MemoryPolicy(remember=("preference",)), bases=(Docs(base="clinica", k=4),)
)

type Talking = tuple[Recording, VoiceBridge, AgentSession[None], FakeLLM]


class Answering:
    """A lookup service that answers both tools, and a rememberer that may fail."""

    def __init__(self, failing: Exception | None = None) -> None:
        self._failing = failing
        self.asked: list[tuple[str, Mapping[str, Any], str | None]] = []
        self.remembered: list[str] = []

    async def lookup(
        self, call: str, tool: PlatformTool, input: Mapping[str, Any], speech_id: str | None
    ) -> Mapping[str, Any]:
        assert call == CALL
        self.asked.append((tool, dict(input), speech_id))
        if tool == "recall":
            return {
                "facts": [{"text": "prefiere la mañana", "source": "call_8", "since": "2026-09-01"}]
            }
        return {"chunks": [{"path": "tarifas.md", "heading": "Tarifas", "text": "Son 45 €."}]}

    async def remember(self, call: str) -> int:
        if self._failing is not None:
            raise self._failing
        self.remembered.append(call)
        return 1


class Slow:
    """A lookup service no turn ever waits out."""

    async def lookup(
        self,
        call: str,  # noqa: ARG002 — the protocol's shape
        tool: PlatformTool,  # noqa: ARG002 — the protocol's shape
        input: Mapping[str, Any],  # noqa: ARG002 — the protocol's shape
        speech_id: str | None,  # noqa: ARG002 — the protocol's shape
    ) -> Mapping[str, Any]:
        """Nothing, half a second from now: past every budget a turn ever sets."""
        await asyncio.sleep(0.5)
        return {"facts": []}


@pytest.fixture
async def answering() -> Answering:
    return Answering()


@pytest.fixture
async def talking(answering: Answering) -> AsyncIterator[Talking]:
    """A headless session on the scripted model, its lookups answered by `answering`."""
    recording = Recording()
    llm = FakeLLM(Scripted(chunks=("Uno.",)), Scripted(chunks=("Dos.",)))
    bridge = build_bridge(a_context(), REMEMBERS, recording, lookup=answering, rememberer=answering)
    live: AgentSession[None] = AgentSession(
        llm=llm, vad=None, turn_handling={"turn_detection": "manual"}
    )
    await bridge.opened(live)
    await live.start(bridge.agent, record=False)  # pyright: ignore[reportUnknownMemberType]
    yield recording, bridge, live, llm
    await live.aclose()


async def test_the_callers_turn_is_the_query_and_the_pair_closes_the_next_request(
    talking: Talking, answering: Answering
) -> None:
    _recording, bridge, live, llm = talking
    await bridge.set_prompt("identity", "You are Clara.")
    await bridge.set_prompt("view", A_VIEW)
    await _the_caller_said(bridge, "quiero un turno")
    await live.generate_reply(user_input="quiero un turno")
    assert [(tool, said["query"]) for tool, said, _speech in answering.asked] == [
        ("recall", "quiero un turno"),
        ("search", "quiero un turno"),
    ]
    (asked,) = llm.asked
    assert [call.name for call in asked.calls] == ["recall", "search"]
    assert [list(json.loads(output.output)) for output in asked.outputs] == [["facts"], ["chunks"]]
    # The view is the last thing in the request, and it is not in a tool result.
    assert asked.system.endswith(A_VIEW)


async def test_recall_and_search_are_declared_to_the_model_beside_the_apps_own_tools(
    talking: Talking,
) -> None:
    _recording, _bridge, live, llm = talking
    await live.generate_reply(user_input="hola")
    (asked,) = llm.asked
    assert asked.tools[-2:] == ("recall", "search")
    assert "find_slot" in asked.tools


async def test_a_class_that_declares_neither_declares_no_platform_tool() -> None:
    recording = Recording()
    llm = FakeLLM(Scripted(chunks=("Uno.",)))
    bridge = build_bridge(a_context(), CLARA, recording)
    live: AgentSession[None] = AgentSession(
        llm=llm, vad=None, turn_handling={"turn_detection": "manual"}
    )
    await bridge.opened(live)
    await live.start(bridge.agent, record=False)  # pyright: ignore[reportUnknownMemberType]
    await live.generate_reply(user_input="hola")
    await live.aclose()
    (asked,) = llm.asked
    assert "recall" not in asked.tools and "search" not in asked.tools


async def test_a_lookup_past_its_budget_is_a_recoverable_entry_and_the_turn_goes_on() -> None:
    recording = Recording()
    bridge = build_bridge(
        a_context(),
        replace(REMEMBERS, bases=()),
        recording,
        lookup=Slow(),
        budgets=Budgets(voice_lookup_ms=0, remember_s=1.0),
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
    assert skipped.data["code"] == "recall_skipped"
    assert skipped.data["recoverable"] is True
    assert "turn.agent" in recording.types


# The defect this closes, measured on a live two-turn call: run at turn end, EVERY lookup was
# skipped, because there the run competes with the reply the session is already generating.
# Started on the interim, the same run is back before the caller stops — so a budget of ZERO, which
# used to skip everything, still puts both pairs in front of the model and writes no skip at all.
async def test_a_lookup_started_while_the_caller_talks_reaches_the_model_on_no_budget() -> None:
    recording = Recording()
    answering = Answering()
    llm = FakeLLM(Scripted(chunks=("Cuarenta euros.",)))
    bridge = build_bridge(
        a_context(),
        REMEMBERS,
        recording,
        lookup=answering,
        budgets=Budgets(voice_lookup_ms=0, remember_s=1.0),
    )
    live: AgentSession[None] = AgentSession(
        llm=llm, vad=None, turn_handling={"turn_detection": "manual"}
    )
    await bridge.opened(live)
    await live.start(bridge.agent, record=False)  # pyright: ignore[reportUnknownMemberType]
    live.emit("user_input_transcribed", _an_interim("cuánto cuesta una revisión"))
    await _the_run_comes_back()
    await _the_caller_said(bridge, "¿Cuánto cuesta una revisión?")
    await live.generate_reply(user_input="¿Cuánto cuesta una revisión?")
    await live.aclose()
    assert [(tool, said["query"]) for tool, said, _speech in answering.asked] == [
        ("recall", "cuánto cuesta una revisión"),
        ("search", "cuánto cuesta una revisión"),
    ]
    (asked,) = llm.asked
    assert [call.name for call in asked.calls] == ["recall", "search"]
    assert [list(json.loads(output.output)) for output in asked.outputs] == [["facts"], ["chunks"]]
    assert recording.of("error") == [], "nothing was skipped: the answers were already here"


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
    bridge = build_bridge(a_context(), REMEMBERS, recording, rememberer=failing)
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
    bridge = build_bridge(a_context(), CLARA, recording, rememberer=answering)
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


# What the session emits mid-sentence, on the way from the recogniser (agent_activity.py:2321).
def _an_interim(text: str) -> session_events.UserInputTranscribedEvent:
    """One interim transcript of the turn being spoken, as livekit hands it to a subscriber."""
    return session_events.UserInputTranscribedEvent(transcript=text, is_final=False)


# An eager run is a task, so the loop has to get a turn before it can have answered.
async def _the_run_comes_back() -> None:
    """Let the lookups the interim started run to completion before the turn ends."""
    await asyncio.sleep(0.05)


async def test_a_lookup_still_running_at_hang_up_is_cancelled_with_the_call() -> None:
    """A task nobody awaits kept asking the platform about a call that had hung up."""
    started = asyncio.Event()

    class _Slow(Answering):
        @override
        async def lookup(self, *args: Any, **kwargs: Any) -> Any:
            started.set()
            await asyncio.Event().wait()

    answering = _Slow()
    bridge = build_bridge(a_context(), REMEMBERS, Recording(), lookup=answering)
    bridge.lookups.heard_so_far("cuánto cuesta una revisión de rutina")
    await started.wait()
    await bridge.lookups.close()
    assert bridge.lookups._running is None  # pyright: ignore[reportPrivateUsage]
