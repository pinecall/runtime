"""The melody a caller hears while a tool runs: the one every agent ships with, and any file."""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import av
from av.error import FFmpegError

# "A New Life", the hold melody of the first Pinecall (sdk-server's assets/hold), made from its mp3
# by converted() below — the 8 kHz wavs that server kept played an octave high on a 16 kHz track.
DEFAULT = Path(__file__).with_name("a-new-life.ogg")

# What every clip becomes, whatever it arrived as: the rate livekit's player mixes at, one channel
# because a phone leg has one, and Opus because a minute of it is half a megabyte, not ten.
RATE = 48_000
_OPUS_FRAME = 960
_BIT_RATE = 64_000

# A melody loops, so a long file buys nothing but storage and a longer wait on the upload.
MAX_SECONDS = 300
MIN_SECONDS = 1

NOT_AUDIO = "that file is no audio this box can read: send a wav, an mp3, an ogg or an m4a"
TOO_LONG = f"a hold melody loops, so {MAX_SECONDS // 60} minutes at most: this one runs longer"
TOO_SHORT = f"a hold melody of less than {MIN_SECONDS} second would stutter as it loops"


class NotAHoldMelody(ValueError):
    """What an upload is refused with, in a sentence the door answers as it is."""


@dataclass(frozen=True)
class Melody:
    """One clip as a call plays it: Ogg Opus, 48 kHz mono, its length, and the hash naming it."""

    audio: bytes
    seconds: float
    sha256: str


def converted(data: bytes) -> Melody:
    """Any file PyAV decodes, as the one form a call plays. Refused in a sentence otherwise."""
    try:
        source = cast(Any, av.open(io.BytesIO(data)))
    except (FFmpegError, OSError, ValueError) as unreadable:
        raise NotAHoldMelody(NOT_AUDIO) from unreadable
    with source:
        if not source.streams.audio:
            raise NotAHoldMelody(NOT_AUDIO)
        try:
            audio, samples = _encoded(source)
        except (FFmpegError, OSError, ValueError) as unreadable:
            raise NotAHoldMelody(NOT_AUDIO) from unreadable
    seconds = samples / RATE
    if seconds < MIN_SECONDS:
        raise NotAHoldMelody(TOO_SHORT)
    return Melody(audio=audio, seconds=round(seconds, 2), sha256=hashlib.sha256(audio).hexdigest())


def _encoded(source: Any) -> tuple[bytes, int]:
    """Decode, resample to 48 kHz mono, and encode Opus in its own frame size. Stops at the cap."""
    out = io.BytesIO()
    resampler = av.AudioResampler(format="s16", layout="mono", rate=RATE)
    fifo = av.AudioFifo()
    samples = 0
    with cast(Any, av.open(out, "w", format="ogg")) as sink:
        stream: Any = sink.add_stream("libopus", rate=RATE)
        stream.layout = "mono"
        stream.bit_rate = _BIT_RATE

        def drain(final: bool) -> None:
            while fifo.samples >= _OPUS_FRAME or (final and fifo.samples > 0):
                frame = fifo.read(min(_OPUS_FRAME, fifo.samples))
                if frame is None:
                    return
                if frame.samples < _OPUS_FRAME:
                    return  # the last few milliseconds: Opus takes whole frames only
                for packet in stream.encode(frame):
                    sink.mux(packet)

        for decoded in source.decode(audio=0):
            for frame in resampler.resample(decoded):
                samples += frame.samples
                if samples > MAX_SECONDS * RATE:
                    raise NotAHoldMelody(TOO_LONG)
                frame.pts = None
                fifo.write(frame)
            drain(final=False)
        for frame in resampler.resample(None):
            samples += frame.samples
            frame.pts = None
            fifo.write(frame)
        drain(final=True)
        for packet in stream.encode(None):
            sink.mux(packet)
    return out.getvalue(), samples
