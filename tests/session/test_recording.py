"""Tests for a call's audio recorded by its own session: the caller left, the other voices right."""

import asyncio
from pathlib import Path

import av
import numpy as np
from livekit.agents import AgentSession

from pinecall.session._recording import RATE, encoded, laid_in, recorded
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
    assert await recorded(live, None, None) is None


async def test_a_session_with_no_audio_records_nothing(tmp_path: Path) -> None:
    assert await recorded(AgentSession(), tmp_path / "audio.ogg", None) is None


async def test_the_caller_is_recorded_left_and_the_agent_right(tmp_path: Path) -> None:
    live: AgentSession[None] = AgentSession()
    live.input.audio = Microphone(tone(300, SPOKEN_S))
    speaker = Speaker()
    live.output.audio = speaker
    audio = tmp_path / "audio.ogg"
    recorder = await recorded(live, audio, None)
    assert recorder is not None
    caller = live.input.audio
    assert caller is not None
    async for _ in caller:
        pass
    agent = live.output.audio
    assert agent is not None
    # A silence between the two, so a frame delivered late under load stays on its own side.
    await asyncio.sleep(SPOKEN_S)
    for frame in tone(500, SPOKEN_S):
        await agent.capture_frame(frame)
        await asyncio.sleep(FRAME_S)
    agent.flush()
    speaker.played()
    await recorder.close(None)
    channels, samples = channels_of(audio)
    assert channels == 2
    caller, agent = (np.abs(samples[side]) for side in (0, 1))
    third = samples.shape[1] // 3
    assert caller[:third].mean() > 10 * caller[-third:].mean()
    assert agent[-third:].mean() > 10 * agent[:third].mean()


def loud_at(samples: np.ndarray, rate: int, start_s: float, end_s: float) -> float:
    """The mean level of a stretch of one channel."""
    return float(np.abs(samples[round(start_s * rate) : round(end_s * rate)]).mean())


def test_a_supervisor_and_the_melody_are_laid_in_on_the_right_where_they_sounded(
    tmp_path: Path,
) -> None:
    seconds = 2.0
    t = np.arange(int(RATE * seconds)) / RATE
    caller = (0.4 * np.sin(2 * np.pi * 300 * t) * (t < 0.5)).astype(np.float32)
    audio = tmp_path / "audio.ogg"
    encoded(np.stack([caller, np.zeros_like(caller)]), audio)
    voice = (8000 * np.sin(2 * np.pi * 500 * t[: RATE // 2])).astype("<i2").tobytes()
    clip = tmp_path / "melody.ogg"
    melody = (0.5 * np.sin(2 * np.pi * 700 * t[: RATE // 4])).astype(np.float32)
    encoded(np.stack([melody, melody]), clip)
    laid_in(audio, [(0.6, bytearray(voice))], (clip, [(1.3, 1.9)]))
    _, samples = channels_of(audio)
    left, right = samples
    rate = 48000
    assert loud_at(left, rate, 0.05, 0.45) > 10 * loud_at(left, rate, 0.7, 1.0)
    assert loud_at(right, rate, 0.7, 1.0) > 10 * loud_at(right, rate, 0.05, 0.45)
    assert loud_at(right, rate, 1.4, 1.8) > 10 * loud_at(right, rate, 1.15, 1.25)
