"""Tests for a call's tracks mixed into one recording: the caller left, the rest right, in time."""

from pathlib import Path

import av
import numpy as np
from av.audio.stream import AudioStream

from pinecall.process.mixing import Placed, mixed

# Egress's tracks and the mix's decode are both at Opus's own 48 kHz.
TRACK_RATE = 48000

# A decoder may overshoot a clipped peak by a little.
OVERSHOOT = 1.05


def a_track(path: Path, hertz: float, seconds: float) -> Path:
    """An Ogg Opus of a tone, as egress writes a track."""
    t = np.arange(int(TRACK_RATE * seconds)) / TRACK_RATE
    tone = (0.5 * np.sin(2 * np.pi * hertz * t)).astype(np.float32)
    with av.open(str(path), mode="w", format="ogg") as sink:
        stream: AudioStream = sink.add_stream("libopus", rate=TRACK_RATE)  # pyright: ignore[reportUnknownMemberType]
        stream.layout = "mono"
        for start in range(0, len(tone), 960):
            frame = av.AudioFrame.from_ndarray(
                tone[None, start : start + 960], format="fltp", layout="mono"
            )
            frame.sample_rate = TRACK_RATE
            frame.pts = start
            for packet in stream.encode(frame):  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
                sink.mux(packet)  # pyright: ignore[reportUnknownMemberType]
        for packet in stream.encode(None):  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            sink.mux(packet)  # pyright: ignore[reportUnknownMemberType]
    return path


def sides_of(path: Path) -> np.ndarray:
    """The mix's samples, one row per channel."""
    with av.open(str(path)) as source:
        stream = source.streams.audio[0]
        assert stream.channels == 2
        return np.concatenate([frame.to_ndarray() for frame in source.decode(stream)], axis=1)


def loud(samples: np.ndarray, start_s: float, end_s: float) -> float:
    """The mean level of a stretch of one channel."""
    return float(np.abs(samples[round(start_s * TRACK_RATE) : round(end_s * TRACK_RATE)]).mean())


def test_each_track_sounds_on_its_side_from_the_moment_it_began(tmp_path: Path) -> None:
    caller = a_track(tmp_path / "caller.ogg", 300, 0.4)
    agent = a_track(tmp_path / "agent.ogg", 500, 0.4)
    out = tmp_path / "mix.ogg"
    mixed([Placed(caller, left=True, at_s=0.0), Placed(agent, left=False, at_s=0.5)], out)
    left, right = sides_of(out)
    assert loud(left, 0.05, 0.35) > 10 * loud(left, 0.55, 0.85)
    assert loud(right, 0.55, 0.85) > 10 * loud(right, 0.05, 0.35)


def test_two_voices_on_one_side_add_up_and_never_pass_full_scale(tmp_path: Path) -> None:
    agent = a_track(tmp_path / "agent.ogg", 500, 0.3)
    melody = a_track(tmp_path / "melody.ogg", 500, 0.3)
    out = tmp_path / "mix.ogg"
    mixed([Placed(agent, left=False, at_s=0.0), Placed(melody, left=False, at_s=0.0)], out)
    _, right = sides_of(out)
    assert np.abs(right).max() <= OVERSHOOT
    assert loud(right, 0.05, 0.25) > 0.4
