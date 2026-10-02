"""A call's tracks mixed into the one recording a player plays: the caller left, the rest right."""

from dataclasses import dataclass
from pathlib import Path

import av
import numpy as np
from av.audio.stream import AudioStream

# The agent's voice comes at 24 kHz and a phone at 8: the mix keeps everything there is at 24.
RATE = 24000

# A sum of voices can pass full scale; it is clipped, not wrapped.
FULL_SCALE = 1.0

# Opus takes whole frames of 20 ms (480 samples at RATE): the mix is padded to a whole number.
OPUS_FRAME = 480


@dataclass(frozen=True)
class Placed:
    """One track's audio file, which side of the mix it sounds on, and when it began in the call."""

    audio: Path
    left: bool
    at_s: float


# Blocking, for a thread: decoded a track at a time, the mix held as two channels of floats.
def mixed(tracks: list[Placed], out: Path) -> None:
    """Write the stereo Ogg Opus of the tracks, each placed at its own time."""
    sides: list[list[np.ndarray]] = [[], []]
    length = 0
    for track in tracks:
        samples = _decoded(track.audio)
        start = round(track.at_s * RATE)
        sides[0 if track.left else 1].append(np.pad(samples, (start, 0)))
        length = max(length, start + len(samples))
    length += -length % OPUS_FRAME
    both = np.zeros((2, length), dtype=np.float32)
    for side, placed in enumerate(sides):
        for samples in placed:
            both[side, : len(samples)] += samples
    np.clip(both, -FULL_SCALE, FULL_SCALE, out=both)
    with av.open(str(out), mode="w", format="ogg") as sink:
        stream: AudioStream = sink.add_stream("libopus", rate=RATE)  # pyright: ignore[reportUnknownMemberType]
        stream.layout = "stereo"
        for start in range(0, length, OPUS_FRAME):
            frame = av.AudioFrame.from_ndarray(
                np.ascontiguousarray(both[:, start : start + OPUS_FRAME]),
                format="fltp",
                layout="stereo",
            )
            frame.sample_rate = RATE
            frame.pts = start
            for packet in stream.encode(frame):  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
                sink.mux(packet)  # pyright: ignore[reportUnknownMemberType]
        for packet in stream.encode(None):  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            sink.mux(packet)  # pyright: ignore[reportUnknownMemberType]


def _decoded(audio: Path) -> np.ndarray:
    resampler = av.AudioResampler(format="flt", layout="mono", rate=RATE)
    pieces: list[np.ndarray] = []
    with av.open(str(audio)) as source:
        for frame in source.decode(audio=0):
            pieces.extend(piece.to_ndarray().reshape(-1) for piece in resampler.resample(frame))
        pieces.extend(piece.to_ndarray().reshape(-1) for piece in resampler.resample(None))
    return np.concatenate(pieces) if pieces else np.zeros(0, dtype=np.float32)
