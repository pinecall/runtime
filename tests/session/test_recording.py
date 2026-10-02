"""Tests for a call's audio recorded by its own session: the caller left, the agent right."""

import asyncio
from pathlib import Path

import av
import numpy as np
from livekit.agents import AgentSession

from pinecall.session._recording import recorded
from tests.fakes.livekit import Microphone, Speaker, tone

FRAME_S = 0.02
SPOKEN_S = 0.4


def channels_of(audio: Path) -> tuple[int, np.ndarray]:
    """The file's channel count and its samples, one row per channel."""
    with av.open(str(audio)) as container:
        stream = container.streams.audio[0]
        decoded = [frame.to_ndarray() for frame in container.decode(stream)]
        return stream.channels, np.concatenate(decoded, axis=1)


async def test_a_call_that_keeps_no_audio_gets_no_recorder() -> None:
    live: AgentSession[None] = AgentSession()
    live.input.audio = Microphone([])
    live.output.audio = Speaker()
    assert await recorded(live, None) is None


async def test_a_session_with_no_audio_records_nothing(tmp_path: Path) -> None:
    assert await recorded(AgentSession(), tmp_path / "audio.ogg") is None


async def test_the_caller_is_recorded_left_and_the_agent_right(tmp_path: Path) -> None:
    live: AgentSession[None] = AgentSession()
    live.input.audio = Microphone(tone(300, SPOKEN_S))
    speaker = Speaker()
    live.output.audio = speaker
    audio = tmp_path / "audio.ogg"
    recorder = await recorded(live, audio)
    assert recorder is not None
    caller = live.input.audio
    assert caller is not None
    async for _ in caller:
        pass
    agent = live.output.audio
    assert agent is not None
    for frame in tone(500, SPOKEN_S):
        await agent.capture_frame(frame)
        await asyncio.sleep(FRAME_S)
    agent.flush()
    speaker.played()
    await recorder.aclose()
    channels, samples = channels_of(audio)
    assert channels == 2
    caller, agent = (np.abs(samples[side]) for side in (0, 1))
    half = samples.shape[1] // 2
    assert caller[:half].mean() > 10 * caller[half:].mean()
    assert agent[half:].mean() > 10 * agent[:half].mean()
