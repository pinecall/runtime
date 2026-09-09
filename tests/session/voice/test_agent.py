"""The agent's transcription node: the words the caller hears reach the log with their timings."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from livekit.agents import stt as recognition
from livekit.agents.types import TimedString
from livekit.agents.voice import ModelSettings

from pinecall.session.voice.agent import VoiceAgent
from pinecall.session.voice.events import Events
from pinecall.session.voice.metrics import Meters
from pinecall.session.voice.writing import Writing
from tests.session.voice.fakes import CALL, Recording, ScriptedSession
from tests.session.voice.test_events import Ended, Speaking

pytestmark = pytest.mark.unit

# What ElevenLabs sends back with `sync_alignment` on: one word per delta, each with the seconds it
# occupies in the reply's audio, exactly as the plugin builds them (elevenlabs/tts.py:1332-1360).
ALIGNED: tuple[TimedString, ...] = (
    TimedString(text="Hay ", start_time=0.0, end_time=0.28),
    TimedString(text="turno ", start_time=0.28, end_time=0.71),
    TimedString(text="a las diez", start_time=0.71, end_time=1.44),
)


class Playing:
    """The bridge as the agent reads it: the view of the moment, and where the words go."""

    def __init__(self, events: Events) -> None:
        self._events = events

    @property
    def view(self) -> str:
        return ""

    def said(self, delta: str | TimedString) -> None:
        self._events.said(delta)

    def heard(self, event: recognition.SpeechEvent) -> bool:  # noqa: ARG002 — the protocol's
        return True


async def a_stream(*deltas: str | TimedString) -> AsyncIterator[str | TimedString]:
    """The transcript livekit hands the node, one delta at a time."""
    for delta in deltas:
        yield delta


async def an_agent(recording: Recording) -> tuple[VoiceAgent, Writing]:
    """The real agent over a real subscriber, writing every entry to a recording gateway."""
    writing = Writing(recording, CALL)
    writing.open()
    events = Events(writing, Meters(writing), Ended())
    events.watch(ScriptedSession(current_speech=Speaking("sp_9")))  # pyright: ignore[reportArgumentType]
    agent = VoiceAgent(instructions="You are Clara.", tools=(), speaking=Playing(events))
    return agent, writing


async def played(*deltas: str | TimedString) -> tuple[Recording, list[str | TimedString]]:
    """One reply through the node: what it forwarded to livekit, and what it left in the log."""
    recording = Recording()
    agent, writing = await an_agent(recording)
    forwarded = [
        delta async for delta in agent.transcription_node(a_stream(*deltas), ModelSettings())
    ]
    await writing.flushed()
    return recording, forwarded


async def test_an_aligned_reply_is_logged_word_by_word_with_livekits_own_timings() -> None:
    recording, _forwarded = await played(*ALIGNED)
    words = recording.of("agent.transcript")
    assert [word.data["text"] for word in words] == ["Hay ", "turno ", "a las diez"]
    assert [(word.data["start"], word.data["end"]) for word in words] == [
        (0.0, 0.28),
        (0.28, 0.71),
        (0.71, 1.44),
    ]
    assert {word.data["speech_id"] for word in words} == {"sp_9"}
    assert {word.ephemeral for word in words} == {None}  # the type's own default, and it is on


async def test_a_voice_that_aligned_nothing_leaves_the_timings_off_the_entry() -> None:
    recording, _forwarded = await played("Hay turno ", TimedString(text="a las diez"))
    for word in recording.of("agent.transcript"):
        assert "start" not in word.data
        assert "end" not in word.data


async def test_every_delta_is_forwarded_to_livekit_timings_and_all() -> None:
    _recording, forwarded = await played(*ALIGNED)
    assert forwarded == list(ALIGNED)
    assert all(isinstance(delta, TimedString) for delta in forwarded)
