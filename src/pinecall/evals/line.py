"""The line a simulated caller speaks over: an interferer under their voice, and packets lost."""

from __future__ import annotations

import math
import random as randomness
from array import array
from collections.abc import Sequence

# What a room publishes: 10 ms of 16-bit mono at a time. livekit's own examples publish at this
# cadence, and it is what a lost packet is a unit of — half a syllable, the way a real one is.
FRAME_MS = 10

# The two ends of a 16-bit sample. A sum that leaves this range is clipped rather than wrapped:
# a wrap is a click the caller never made, and the STT would hear it as one.
LOUDEST = 32767
QUIETEST = -32768

# Silence is what a jitter buffer plays for a packet that never arrived, so it is what a dropped
# frame becomes here — the line goes quiet for 10 ms, which is exactly the artefact being tested.
LOST = b"\x00\x00"

# What those calls measured a television at when it started taking the conversation over: 15 dB
# under the caller was already enough for two of its sentences to become `turn.user`
# (docs/decisions/voice-bridge.md).
# It is the default for that reason and not because it is a round number.
UNDER_THE_CALLER_DB = 15.0


def frames_of(pcm: bytes, sample_rate: int, ms: int = FRAME_MS) -> list[bytes]:
    """The audio cut into the packets a room carries, the last one padded out with silence."""
    per_frame = (sample_rate * ms // 1000) * 2
    frames = [pcm[at : at + per_frame] for at in range(0, len(pcm), per_frame)]
    if frames and len(frames[-1]) < per_frame:
        frames[-1] = frames[-1] + LOST * ((per_frame - len(frames[-1])) // 2)
    return frames


def rms_of(pcm: bytes) -> float:
    """How loud this audio is, as the one number a level is set against."""
    samples = array("h")
    samples.frombytes(pcm)
    if not samples:
        return 0.0
    return math.sqrt(sum(float(sample) * sample for sample in samples) / len(samples))


def mixed(caller: bytes, interferer: bytes, db_under: float = UNDER_THE_CALLER_DB) -> bytes:
    """The caller's voice with the interferer under it, at the level the run asked for, in dB."""
    if not interferer or not caller:
        return caller
    level = _the_level_that_sits(rms_of(caller), rms_of(interferer), db_under)
    voice = array("h")
    voice.frombytes(caller)
    noise = array("h")
    noise.frombytes(interferer)
    # The interferer runs as long as the caller does: a television does not stop talking because
    # the sentence it was mixed into was longer than the bulletin it was cut from.
    for at in range(len(voice)):
        voice[at] = _clipped(voice[at] + int(noise[at % len(noise)] * level))
    return voice.tobytes()


def with_losses(
    frames: Sequence[bytes], loss: float, random: randomness.Random | None = None
) -> list[bytes]:
    """The same packets with a share of them never sent: what the far end plays is silence."""
    if loss <= 0:
        return list(frames)
    dice = random or randomness.Random()  # noqa: S311 — noise on a line is not a secret
    return [LOST * (len(frame) // 2) if dice.random() < loss else frame for frame in frames]


def said_of(db_under: float, loss: float) -> str:
    """The one line a report prints about the line the call was held on."""
    noise = f"interferer {db_under:.0f} dB under the caller"
    return noise if loss <= 0 else f"{noise}, {loss * 100:.0f}% packet loss"


def _the_level_that_sits(caller: float, interferer: float, db_under: float) -> float:
    """What to multiply the interferer by so it sits that many dB below the caller's own voice."""
    if interferer == 0:
        return 0.0
    return (caller / interferer) * (10 ** (-db_under / 20))


def _clipped(sample: int) -> int:
    """A sum that left the 16-bit range, held at its edge rather than wrapped into a click."""
    return max(QUIETEST, min(LOUDEST, sample))
