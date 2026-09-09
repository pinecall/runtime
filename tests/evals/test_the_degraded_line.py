"""The line a simulated caller speaks over: the interferer sits where it was asked to sit."""

from __future__ import annotations

import math
import random
from array import array

from pinecall.evals.line import (
    FRAME_MS,
    LOST,
    frames_of,
    mixed,
    rms_of,
    said_of,
    with_losses,
)

RATE = 48_000

# One second of audio, cut at this module's own frame size: what every fixture here is.
A_SECOND_IN_FRAMES = 100

# 16-bit samples are integers, so a level asked for in dB lands near it and not on it.
WITHIN_THE_ROUNDING_DB = 0.5


def a_tone(samples: int, amplitude: int, period: int = 100) -> bytes:
    """Something with a level to measure: a tone, because silence has no level to sit under."""
    written = array("h")
    for at in range(samples):
        written.append(int(amplitude * math.sin(2 * math.pi * at / period)))
    return written.tobytes()


def test_a_frame_is_ten_milliseconds_of_the_rate_it_was_cut_at() -> None:
    """A lost packet is a unit of the line, and 10 ms is what a room carries at a time."""
    frames = frames_of(a_tone(RATE, 8000), RATE)

    assert FRAME_MS == 10
    assert len(frames) == A_SECOND_IN_FRAMES
    assert all(len(frame) == RATE // A_SECOND_IN_FRAMES * 2 for frame in frames)


def test_the_last_frame_is_padded_with_silence_rather_than_sent_short() -> None:
    """A short frame declares more samples than it carries and the room refuses it."""
    frames = frames_of(a_tone(RATE // 100 + 10, 8000), RATE)

    assert len(frames) == 2
    assert len(frames[1]) == len(frames[0])
    assert frames[1].endswith(LOST * 10)


def test_the_interferer_ends_up_the_asked_for_number_of_decibels_under_the_caller() -> None:
    """15 dB under is what tk-4009d9 measured a television taking a conversation over at."""
    caller = a_tone(RATE, 8000, period=97)
    television = a_tone(RATE, 8000, period=31)

    with_noise = mixed(caller, television, db_under=15.0)

    # What was added is the mix minus the caller, and its level is what the number claims.
    added = _difference(with_noise, caller)
    under = 20 * math.log10(rms_of(caller) / rms_of(added))
    assert abs(under - 15.0) < WITHIN_THE_ROUNDING_DB


def test_a_quieter_interferer_is_the_larger_number_of_decibels() -> None:
    """The dB is under the caller, so 25 must be quieter than 15 — never the other way round."""
    caller = a_tone(RATE, 8000, period=97)
    television = a_tone(RATE, 8000, period=31)

    near = _difference(mixed(caller, television, 15.0), caller)
    far = _difference(mixed(caller, television, 25.0), caller)

    assert rms_of(far) < rms_of(near)


def test_a_line_with_nothing_to_mix_in_is_the_caller_untouched() -> None:
    """No interferer is a clean line, and a clean line is not a copy that went through the mixer."""
    caller = a_tone(RATE, 8000)

    assert mixed(caller, b"", 15.0) == caller


def test_the_interferer_runs_as_long_as_the_caller_does() -> None:
    """A television does not stop talking because the sentence it was mixed into was longer."""
    caller = a_tone(RATE, 8000, period=97)
    a_short_bulletin = a_tone(RATE // 10, 8000, period=31)

    with_noise = mixed(caller, a_short_bulletin, 15.0)

    assert rms_of(_difference(with_noise, caller)[-RATE:]) > 0


def test_a_lost_packet_is_the_silence_a_jitter_buffer_plays() -> None:
    """Not a dropped frame: the far end plays 10 ms of nothing, which is the artefact tested."""
    frames = frames_of(a_tone(RATE, 8000), RATE)

    thinned = with_losses(frames, 0.5, random.Random(7))

    assert len(thinned) == len(frames)
    silent = [frame for frame in thinned if set(frame) == {0}]
    assert 35 < len(silent) < 65


def test_no_loss_leaves_every_packet_exactly_as_it_was() -> None:
    """A run that asked for nothing must not have its audio rewritten by the loss model at all."""
    frames = frames_of(a_tone(RATE, 8000), RATE)

    assert with_losses(frames, 0.0) == frames


def test_the_line_is_described_in_the_words_a_report_prints() -> None:
    """The report says what the call was held on; a number nobody can read is not a finding."""
    assert said_of(15.0, 0.0) == "interferer 15 dB under the caller"
    assert said_of(22.0, 0.02) == "interferer 22 dB under the caller, 2% packet loss"


def _difference(mix: bytes, caller: bytes) -> bytes:
    """What the mixer added to the caller's own voice: the interferer, at the level it landed."""
    mixed_samples = array("h")
    mixed_samples.frombytes(mix)
    caller_samples = array("h")
    caller_samples.frombytes(caller)
    return array("h", (a - b for a, b in zip(mixed_samples, caller_samples, strict=True))).tobytes()
