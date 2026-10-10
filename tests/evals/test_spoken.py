"""Tests for a persona on a real line: the audio it says, when it may speak, when it hangs up."""

import math
from array import array

from pinecall.evals import spoken
from pinecall.evals.spoken import (
    FRAME_MS,
    LOST,
    SAMPLE_RATE,
    Line,
    described,
    drop_packets,
    frames_of,
    mix_interferer,
    pick_caller_voice,
    resample_to_room,
    rms_of,
)

A_SECOND_IN_FRAMES = 100


# Integer samples put the level near the target, never exactly on it.
WITHIN_THE_ROUNDING_DB = 0.5


VOICES = {"acme/es": "marcos", "acme/en": "henry", "acme/fr": "amelie", "other/es": "lucia"}


LINES = ("Esa me viene bien.", "Sí, confírmemela.")


def tone_of(samples: int, amplitude: int, period: int = 100) -> bytes:
    written = array("h")
    for index in range(samples):
        written.append(int(amplitude * math.sin(2 * math.pi * index / period)))
    return written.tobytes()


def difference(mix: bytes, caller: bytes) -> bytes:
    mixed, voice = array("h"), array("h")
    mixed.frombytes(mix)
    voice.frombytes(caller)
    return array("h", (a - b for a, b in zip(mixed, voice, strict=True))).tobytes()


# ── the line ──


def test_a_frame_is_ten_milliseconds_of_the_rate_it_was_cut_at() -> None:
    frames = frames_of(tone_of(SAMPLE_RATE, 8000), SAMPLE_RATE)
    assert FRAME_MS == 10
    assert len(frames) == A_SECOND_IN_FRAMES
    assert all(len(frame) == SAMPLE_RATE // A_SECOND_IN_FRAMES * 2 for frame in frames)


def test_the_last_frame_is_padded_with_silence_rather_than_sent_short() -> None:
    frames = frames_of(tone_of(SAMPLE_RATE // 100 + 10, 8000), SAMPLE_RATE)
    assert len(frames) == 2
    assert len(frames[1]) == len(frames[0])
    assert frames[1].endswith(LOST * 10)


def test_the_interferer_ends_up_the_asked_for_number_of_decibels_under_the_caller() -> None:
    caller = tone_of(SAMPLE_RATE, 8000, period=97)
    added = difference(mix_interferer(caller, tone_of(SAMPLE_RATE, 8000, period=31), 15.0), caller)
    under = 20 * math.log10(rms_of(caller) / rms_of(added))
    assert abs(under - 15.0) < WITHIN_THE_ROUNDING_DB


def test_a_quieter_interferer_is_the_larger_number_of_decibels() -> None:
    caller = tone_of(SAMPLE_RATE, 8000, period=97)
    television = tone_of(SAMPLE_RATE, 8000, period=31)
    near = difference(mix_interferer(caller, television, 15.0), caller)
    far = difference(mix_interferer(caller, television, 25.0), caller)
    assert rms_of(far) < rms_of(near)


def test_a_line_with_nothing_to_mix_in_is_the_caller_untouched() -> None:
    caller = tone_of(SAMPLE_RATE, 8000)
    assert mix_interferer(caller, b"", 15.0) == caller


def test_the_interferer_runs_as_long_as_the_caller_does() -> None:
    caller = tone_of(SAMPLE_RATE, 8000, period=97)
    with_noise = mix_interferer(caller, tone_of(SAMPLE_RATE // 10, 8000, period=31), 15.0)
    assert rms_of(difference(with_noise, caller)[-SAMPLE_RATE:]) > 0


def test_a_lost_packet_is_the_silence_a_jitter_buffer_plays() -> None:
    frames = frames_of(tone_of(SAMPLE_RATE, 8000), SAMPLE_RATE)
    thinned = drop_packets(frames, Line(packet_loss=0.5))
    assert len(thinned) == len(frames)
    # Half of a hundred, by chance: outside 20 to 80 once in a billion runs.
    assert 20 < len([frame for frame in thinned if set(frame) == {0}]) < 80


def test_no_loss_leaves_every_packet_exactly_as_it_was() -> None:
    frames = frames_of(tone_of(SAMPLE_RATE, 8000), SAMPLE_RATE)
    assert drop_packets(frames, Line()) == frames


def test_the_line_is_described_in_the_words_a_report_prints() -> None:
    assert described(Line()) == "a clean line"
    assert described(Line(interferer_db=15.0)) == "interferer 15 dB under the caller"
    assert described(Line(interferer_db=22.0, packet_loss=0.02)) == (
        "interferer 22 dB under the caller, 2% packet loss"
    )


# ── the caller's voice ──


def test_audio_already_at_the_rooms_rate_comes_back_untouched() -> None:
    audio = tone_of(480, 8000)
    assert resample_to_room(audio, SAMPLE_RATE) == audio


def test_audio_at_the_vendors_rate_comes_back_as_a_second_at_the_rooms() -> None:
    second = resample_to_room(tone_of(24_000, 8000), 24_000)
    assert abs(len(second) // 2 - SAMPLE_RATE) < SAMPLE_RATE // 50


def test_the_caller_speaks_in_the_first_voice_of_its_language_when_the_agent_has_another() -> None:
    assert pick_caller_voice(VOICES, "acme", "someone-else", "es-ES") == "marcos"


def test_an_agent_that_speaks_in_that_voice_has_its_caller_in_another() -> None:
    assert pick_caller_voice(VOICES, "acme", "marcos", "es") == "henry"


def test_a_language_with_no_voice_of_its_own_is_called_in_englishs() -> None:
    assert pick_caller_voice(VOICES, "acme", None, "de") == "henry"


def test_no_caller_voice_is_the_one_the_agent_is_given() -> None:
    for agents_voice in VOICES.values():
        assert pick_caller_voice(VOICES, "acme", agents_voice, "es") != agents_voice


def test_a_vendor_the_operator_gave_no_voice_speaks_with_its_own() -> None:
    assert pick_caller_voice(VOICES, "nobody", None, "es") is None


def test_the_room_the_caller_joins_is_the_one_it_was_given() -> None:
    line = spoken.SpokenLine("ws://box", "t", "call_1", 3, Line())
    assert (line.url, line.call, line.turns) == ("ws://box", "call_1", 3)
