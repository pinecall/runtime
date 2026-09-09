"""One spoken call end to end on livekit's own session: the entries, the read-back, the summary."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
from livekit.agents import llm as agents
from livekit.agents import stt as recognition
from livekit.agents.voice import AgentSession
from livekit.agents.voice.events import CloseReason
from livekit.agents.voice.turn import TurnHandlingOptions

from pinecall.session.voice import VoiceBridge, a_bridge
from pinecall.session.voice.voice import HOW_IT_ENDED
from pinecall_protocol.defs import ToolResult
from tests.session.fake_llm import FakeLLM, Scripted, a_call
from tests.session.voice.fakes import BOOK, CLARA, Recording
from tests.session.voice.fakes import a_call as a_context
from tests.session.voice.silence import SilentEars

pytestmark = pytest.mark.unit

A_USAGE = agents.CompletionUsage(completion_tokens=12, prompt_tokens=300, total_tokens=312)


type Talking = tuple[Recording, VoiceBridge, AgentSession[None]]

# A session a test drives itself: the channel says when a turn ended, nothing listens for silence.
BY_HAND: TurnHandlingOptions = {"turn_detection": "manual"}


@pytest.fixture
async def talking(llm_script: tuple[Scripted, ...]) -> AsyncIterator[Talking]:
    """A headless session on the scripted model, the bridge opened on it, the call started."""
    recording = Recording(tools={"book": ToolResult(call_id="", name="book", output={"ref": "A7"})})
    bridge = a_bridge(a_context(), CLARA, recording)
    live: AgentSession[None] = AgentSession(
        llm=FakeLLM(*llm_script), vad=None, turn_handling={"turn_detection": "manual"}
    )
    await bridge.opened(live)
    await live.start(bridge.agent, record=False)  # pyright: ignore[reportUnknownMemberType]
    yield recording, bridge, live


async def test_the_ears_are_told_the_names_the_state_is_holding_the_moment_it_moves() -> None:
    """The class already knows who it is talking to: a clinic cannot declare its patients, but
    the tool that identified Ana just wrote her name, and the next turn is where it is said."""
    recording = Recording()
    bridge = a_bridge(a_context(), replace(CLARA, hears=("Clínica Norte",)), recording)
    live: AgentSession[None] = AgentSession(
        llm=FakeLLM(), stt=SilentEars(keyterms=True), vad=None, turn_handling=BY_HAND
    )
    # Not started: the keyterms a session holds are the detector's, and it is asked before the
    # first turn as readily as after one (agent_session.py:777-779, 1366).
    await bridge.opened(live)
    await bridge.set_state({"patient": {"name": "Ana García"}}, ["patient"])
    assert live.keyterms == ["Clínica Norte", "Ana García"]


async def test_ears_with_no_keyterms_door_are_never_told_anything() -> None:
    """Soniox is the default vendor and advertises none; telling it anyway logs a warning per
    call and changes nothing (stt/stt.py:293-298). Its declared words ride `context` instead."""
    recording = Recording()
    bridge = a_bridge(a_context(), replace(CLARA, hears=("Clínica Norte",)), recording)
    live: AgentSession[None] = AgentSession(
        llm=FakeLLM(), stt=SilentEars(), vad=None, turn_handling=BY_HAND
    )
    await bridge.opened(live)
    await bridge.set_state({"patient": {"name": "Ana García"}}, ["patient"])
    assert live.keyterms == []


@pytest.mark.parametrize(
    "llm_script", [(Scripted(chunks=("Hola, ", "soy Clara."), usage=A_USAGE),)]
)
async def test_one_turn_writes_the_started_the_turns_the_metrics_and_the_summary(
    talking: Talking,
) -> None:
    recording, bridge, live = talking
    await live.generate_reply(user_input="hola")
    await bridge.closed("the test hung up")
    types = recording.types
    assert types[0] == "call.started"
    assert types[-3:] == ["call.ended", "call.summary", "call.score"]
    assert types.index("turn.user") < types.index("metrics.llm") < types.index("turn.agent")
    (user,) = recording.of("turn.user")
    (agent,) = recording.of("turn.agent")
    assert (user.data["text"], agent.data["text"]) == ("hola", "Hola, soy Clara.")
    assert agent.data["interrupted"] is False


@pytest.mark.parametrize("llm_script", [(Scripted(chunks=("Hola.",), usage=A_USAGE),)])
async def test_the_metrics_block_carries_the_speech_it_belongs_to_and_the_summary_the_cost(
    talking: Talking,
) -> None:
    """The block is written one loop tick after the component emitted it, so it carries the
    speech_id livekit's own listener stamped on it (agent_activity.py:1971), whoever ran first."""
    recording, bridge, live = talking
    await live.generate_reply(user_input="hola")
    await bridge.closed("the test hung up")
    (block,) = recording.of("metrics.llm")
    (agent,) = recording.of("turn.agent")
    assert block.data["speech_id"] == agent.data["speech_id"]
    assert block.data["prompt_tokens"] == 300
    (summary,) = recording.of("call.summary")
    rows = summary.data["usage"]
    assert [row["type"] for row in rows] == ["llm_usage"]
    assert rows[0]["input_tokens"] == 300
    assert summary.data["cost"]["eur"] > 0
    assert summary.data["turns"] == 1


@pytest.mark.parametrize(
    "llm_script",
    [
        (
            Scripted(chunks=("Voy. ",), calls=(a_call("bk_1", "book", {"at": "10:15"}),)),
            Scripted(chunks=("Listo.",)),
            Scripted(chunks=("De nada.",)),
        )
    ],
)
async def test_the_read_back_lands_after_the_output_and_before_the_callers_next_words(
    talking: Talking,
) -> None:
    """Criterion 2: a confirm tool runs straight through; its read-back is spoken and enters the
    model's history as the agent's own words, behind the tool output and ahead of the caller."""
    recording, bridge, live = talking
    await live.generate_reply(user_input="reservame el de las 10:15")
    await _settled(live)
    await live.generate_reply(user_input="gracias")
    await bridge.closed("the test hung up")
    items = live.history.items
    kinds = [_kind(item) for item in items]
    output_at = kinds.index("output:bk_1")
    read_back_at = kinds.index("assistant:Le reservé el turno de las 10:15, referencia A7.")
    thanks_at = kinds.index("user:gracias")
    assert output_at < read_back_at < thanks_at
    assert not recording.of("confirm.request")
    assert "Le reservé el turno de las 10:15, referencia A7." in [
        turn.data["text"] for turn in recording.of("turn.agent")
    ]
    assert BOOK.name in {asked.use.name for asked in recording.asked}


@pytest.mark.parametrize(
    ("closed_for", "ended"),
    [
        (CloseReason.JOB_SHUTDOWN, ("drained", "platform")),
        (CloseReason.USER_INITIATED, ("drained", "platform")),
        (CloseReason.PARTICIPANT_DISCONNECTED, ("caller_hung_up", "caller")),
        (CloseReason.ERROR, ("error", "platform")),
    ],
)
def test_a_worker_the_platform_took_down_is_drained_and_never_an_error(
    closed_for: CloseReason, ended: tuple[str, str]
) -> None:
    """A deploy that drains a worker mid-call is nobody's fault, and the log must not say error."""
    assert HOW_IT_ENDED[closed_for] == ended


async def test_the_summary_points_at_the_recording_the_bridge_was_born_knowing() -> None:
    recording = Recording()
    bridge = a_bridge(a_context(), CLARA, recording, Path("recordings/call_1/audio.ogg"))
    live: AgentSession[None] = AgentSession(llm=FakeLLM(), vad=None)
    await bridge.opened(live)
    await live.start(bridge.agent, record=False)  # pyright: ignore[reportUnknownMemberType]
    await bridge.closed("the test hung up")
    (summary,) = recording.of("call.summary")
    assert summary.data["recording"] == "recordings/call_1/audio.ogg"


@pytest.mark.parametrize("llm_script", [()])
async def test_a_backchannel_over_the_agents_voice_never_reaches_the_model(
    talking: Talking,
) -> None:
    _recording, bridge, live = talking
    live._agent_state = "speaking"  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
    assert bridge.heard(_final("sí, sí")) is False
    assert bridge.heard(_final("no, el martes no")) is True
    live._agent_state = "listening"  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
    assert bridge.heard(_final("sí")) is True
    await bridge.closed("the test hung up")


async def _settled(live: AgentSession[None]) -> None:
    """Wait until the say() the bridge scheduled has played and entered the history."""
    for _ in range(100):
        speech = live.current_speech
        if speech is not None:
            await speech
        elif live.agent_state in ("listening", "idle"):
            return
        await asyncio.sleep(0.01)


def _kind(item: agents.ChatItem) -> str:
    """One history item as a short label a test can find in a list."""
    if isinstance(item, agents.ChatMessage):
        return f"{item.role}:{item.text_content or ''}"
    if isinstance(item, agents.FunctionCall):
        return f"call:{item.call_id}"
    if isinstance(item, agents.FunctionCallOutput):
        return f"output:{item.call_id}"
    return type(item).__name__


def _final(text: str) -> recognition.SpeechEvent:
    """A final transcript as the recogniser hands one over."""
    return recognition.SpeechEvent(
        type=recognition.SpeechEventType.FINAL_TRANSCRIPT,
        alternatives=[recognition.SpeechData(language=cast("Any", "es"), text=text)],
    )
