"""A scripted session's events become entries, in the order livekit produced them, whole."""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest
from livekit.agents import APIStatusError
from livekit.agents import llm as agents
from livekit.agents.llm.chat_context import MetricsReport
from livekit.agents.metrics import LLMMetrics
from livekit.agents.metrics.usage import AgentSessionUsage, LLMModelUsage
from livekit.agents.tts import TTSError
from livekit.agents.voice import events as session_events

from pinecall.session.voice.events import Events
from pinecall.session.voice.log_writer import Writing
from pinecall.session.voice.metrics import Meters
from tests.session.voice.fakes import CALL, Recording, ScriptedSession

pytestmark = pytest.mark.unit

# livekit types a language as a closed Literal of codes; the string a recogniser reports is one.
SPANISH = cast("Any", "es")


class Speaking:
    """livekit's SpeechHandle, as far as the bridge reads it: an id."""

    def __init__(self, id: str) -> None:
        self.id = id


def heard(text: str, final: bool) -> session_events.UserInputTranscribedEvent:
    """What the recogniser said, as the session forwards it."""
    return session_events.UserInputTranscribedEvent(
        transcript=text, is_final=final, language=SPANISH
    )


def user_state(new: Any) -> session_events.UserStateChangedEvent:
    return session_events.UserStateChangedEvent(old_state="listening", new_state=new)


def agent_state(new: Any) -> session_events.AgentStateChangedEvent:
    return session_events.AgentStateChangedEvent(old_state="listening", new_state=new)


def added(item: agents.ChatMessage) -> session_events.ConversationItemAddedEvent:
    return session_events.ConversationItemAddedEvent(item=item)


class Ended:
    """The bridge, as far as the subscriber reaches it: the one call it can end for good."""

    def __init__(self) -> None:
        self.causes: list[str] = []

    def ends_for(self, cause: str) -> None:
        self.causes.append(cause)


class Overheard:
    """The lookups, as far as the subscriber reaches them: the words a run could start on."""

    def __init__(self) -> None:
        self.said: list[str] = []

    def heard_so_far(self, said: str) -> None:
        self.said.append(said)


async def a_bridge_on(
    session: ScriptedSession,
    ending: Ended | None = None,
    listening: Overheard | None = None,
) -> tuple[Recording, Events, Writing]:
    """The events subscriber on a scripted session, writing to a recording gateway."""
    recording = Recording()
    writing = Writing(recording, CALL)
    writing.open()
    events = Events(writing, Meters(writing), ending or Ended(), listening or Overheard())
    events.watch(session)  # pyright: ignore[reportArgumentType]
    return recording, events, writing


async def test_the_callers_words_and_states_land_in_the_order_they_were_heard() -> None:
    session = ScriptedSession()
    recording, _events, writing = await a_bridge_on(session)
    session.emit("user_state_changed", user_state("speaking"))
    session.emit("user_input_transcribed", heard("hola", final=False))
    session.emit("user_input_transcribed", heard("hola, quiero un turno", final=True))
    session.emit("agent_state_changed", agent_state("thinking"))
    await writing.flushed()
    assert recording.types == ["user.state", "user.transcript", "user.transcript", "agent.state"]
    interim, final = recording.of("user.transcript")
    assert (interim.ephemeral, final.ephemeral) == (True, False)
    assert final.data == {"text": "hola, quiero un turno", "final": True, "language": "es"}
    assert recording.of("agent.state")[0].data == {"state": "thinking"}


# The interim is the one moment the platform hears the caller mid-sentence, and it is what the
# lookups start on. The final is already the turn, and the turn end runs its own path.
async def test_only_the_interim_transcript_reaches_the_lookups_and_never_the_final() -> None:
    session = ScriptedSession()
    overheard = Overheard()
    _recording, _events, writing = await a_bridge_on(session, listening=overheard)
    session.emit("user_input_transcribed", heard("cuánto cuesta", final=False))
    session.emit("user_input_transcribed", heard("cuánto cuesta una revi", final=False))
    session.emit("user_input_transcribed", heard("cuánto cuesta una revisión", final=True))
    await writing.flushed()
    assert overheard.said == ["cuánto cuesta", "cuánto cuesta una revi"]


async def test_a_user_turn_carries_its_report_whole_and_the_language_the_recogniser_said() -> None:
    session = ScriptedSession(current_speech=Speaking("sp_1"))
    recording, _events, writing = await a_bridge_on(session)
    session.emit("user_input_transcribed", heard("hola", final=True))
    report: MetricsReport = {
        "started_speaking_at": 10.0,
        "stopped_speaking_at": 11.5,
        "transcription_delay": 0.2,
        "end_of_turn_delay": 0.4,
        "on_user_turn_completed_delay": 0.01,
    }
    message = agents.ChatMessage(role="user", content=["hola"], metrics=report)
    session.emit("conversation_item_added", added(message))
    await writing.flushed()
    (turn,) = recording.of("turn.user")
    assert turn.data["speech_id"] == "sp_1"
    assert turn.data["language"] == "es"
    assert turn.data["metrics"] == report
    (eou,) = recording.of("metrics.eou")
    assert eou.data["end_of_utterance_delay"] == 0.4
    assert eou.data["speech_id"] == "sp_1"
    assert eou.data["metadata"] == {"model_name": "unknown", "model_provider": "manual"}


async def test_an_agent_turn_carries_its_report_whole_and_whether_it_was_cut_off() -> None:
    session = ScriptedSession(current_speech=Speaking("sp_2"))
    recording, events, writing = await a_bridge_on(session)
    report: MetricsReport = {
        "llm_node_ttft": 0.31,
        "llm_node_tps": 80.0,
        "tts_node_ttfb": 0.12,
        "playback_latency": 0.05,
        "e2e_latency": 0.62,
        "provider_request_ids": ["req_1"],
        "llm_metadata": {"model_name": "claude-haiku-4-5", "model_provider": "anthropic"},
    }
    reply = agents.ChatMessage(
        role="assistant", content=["Hay a las 10"], interrupted=True, metrics=report
    )
    session.emit("conversation_item_added", added(reply))
    await writing.flushed()
    (turn,) = recording.of("turn.agent")
    assert (turn.data["speech_id"], turn.data["interrupted"]) == ("sp_2", True)
    assert turn.data["metrics"] == report
    assert events.last_said == "Hay a las 10"


async def test_a_played_delta_is_an_ephemeral_transcript_of_the_speech_in_flight() -> None:
    session = ScriptedSession(current_speech=Speaking("sp_3"))
    recording, events, writing = await a_bridge_on(session)
    events.said("Hay ")
    await writing.flushed()
    (delta,) = recording.of("agent.transcript")
    assert delta.data == {"speech_id": "sp_3", "text": "Hay ", "final": False}
    assert delta.ephemeral is None  # the protocol's own default for the type, which is ephemeral


async def test_the_usage_rows_are_kept_for_the_summary_and_an_error_is_written_verbatim() -> None:
    session = ScriptedSession()
    recording, events, writing = await a_bridge_on(session)
    row = LLMModelUsage(provider="anthropic", model="claude-haiku-4-5", input_tokens=10)
    usage = AgentSessionUsage(model_usage=[row])
    session.emit("session_usage_updated", session_events.SessionUsageUpdatedEvent(usage=usage))
    failed = agents.LLMError(
        type="llm_error",
        timestamp=0.0,
        label="x",
        error=RuntimeError("the vendor said 529"),
        recoverable=True,
    )
    session.emit("error", session_events.ErrorEvent(error=failed, source=None))  # pyright: ignore[reportArgumentType]
    await writing.flushed()
    (error,) = recording.of("error")
    assert "529" in error.data["message"]
    assert error.data["recoverable"] is True
    (kept,) = events._meters.rows  # pyright: ignore[reportPrivateUsage]
    assert kept.model_dump()["input_tokens"] == 10


async def test_a_voice_that_does_not_exist_ends_the_call_after_one_error_and_no_retry() -> None:
    """1008 voice_id_does_not_exist is the same answer forever: seven of these was the bug."""
    session = ScriptedSession()
    ending = Ended()
    recording, _events, writing = await a_bridge_on(session, ending)
    refused = TTSError(
        type="tts_error",
        timestamp=0.0,
        label="elevenlabs",
        error=APIStatusError("voice_id_does_not_exist: no voice carolina", status_code=1008),
        recoverable=True,
    )
    for _ in range(3):
        session.emit("error", session_events.ErrorEvent(error=refused, source=None))  # pyright: ignore[reportArgumentType]
    await writing.flushed()
    (error,) = recording.of("error")
    assert error.data["code"] == "component_dead_end"
    assert "voice_id_does_not_exist" in error.data["message"]
    assert error.data["recoverable"] is False
    assert len(ending.causes) == 1
    assert ending.causes[0].startswith("tts_error: ")


async def test_a_vendor_having_a_bad_minute_is_written_and_the_call_goes_on() -> None:
    """503 is the moment, not the request: livekit's own retry is right, and nothing ends."""
    session = ScriptedSession()
    ending = Ended()
    recording, _events, writing = await a_bridge_on(session, ending)
    busy = TTSError(
        type="tts_error",
        timestamp=0.0,
        label="elevenlabs",
        error=APIStatusError("service unavailable", status_code=503),
        recoverable=True,
    )
    for _ in range(2):
        session.emit("error", session_events.ErrorEvent(error=busy, source=None))  # pyright: ignore[reportArgumentType]
    await writing.flushed()
    assert [entry.data["code"] for entry in recording.of("error")] == ["component_failed"] * 2
    assert ending.causes == []


async def test_letting_go_of_the_session_leaves_no_listener_behind() -> None:
    session = ScriptedSession()
    _recording, events, _writing = await a_bridge_on(session)
    assert sum(len(callbacks) for callbacks in session.listeners.values()) == 6
    events.stop()
    assert sum(len(callbacks) for callbacks in session.listeners.values()) == 0


async def test_a_block_is_written_after_every_listener_saw_it_so_the_speech_id_is_there() -> None:
    """livekit stamps speech_id in its own listener, and a set of listeners has no order."""
    recording = Recording()
    writing = Writing(recording, CALL)
    writing.open()
    meters = Meters(writing)
    block = LLMMetrics(
        label="x",
        request_id="r",
        timestamp=0.0,
        duration=0.1,
        ttft=0.05,
        cancelled=False,
        completion_tokens=1,
        prompt_tokens=2,
        prompt_cached_tokens=0,
        total_tokens=3,
        tokens_per_second=10.0,
    )
    meters.collected(block)
    block.speech_id = "sp_late"
    await asyncio.sleep(0)
    await writing.flushed()
    assert recording.of("metrics.llm")[0].data["speech_id"] == "sp_late"
