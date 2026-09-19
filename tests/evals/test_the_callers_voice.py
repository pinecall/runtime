"""The caller's voice: a person's, never the agent's, and always at the room's rate."""

import pytest

from pinecall.evals import speech
from pinecall.providers.tts.voices import VOICES

pytestmark = pytest.mark.unit

# One second of silence, 16-bit mono, at a rate the vendor might answer at.
A_SECOND_AT = {rate: b"\x00\x00" * rate for rate in (22_050, speech.SAMPLE_RATE)}


def test_audio_already_at_the_rooms_rate_comes_back_untouched() -> None:
    pcm = speech.at_the_rooms_rate(A_SECOND_AT[speech.SAMPLE_RATE], speech.SAMPLE_RATE)

    assert pcm == A_SECOND_AT[speech.SAMPLE_RATE]


# The caller's track is opened at the room's rate before a word exists, so what the vendor sent is
# brought to that rate, and a second of speech is still a second of speech when it is pushed.
def test_audio_at_the_vendors_rate_comes_back_as_a_second_at_the_rooms() -> None:
    pcm = speech.at_the_rooms_rate(A_SECOND_AT[22_050], 22_050)

    samples = len(pcm) // 2
    assert abs(samples - speech.SAMPLE_RATE) < speech.SAMPLE_RATE // 100, (
        "a second in should be a second out, within the resampler's own edge"
    )


def test_the_caller_speaks_in_the_first_caller_voice_when_the_agent_has_another() -> None:
    assert speech.a_callers_voice(VOICES["carolina"].voice_id) == speech.CALLER_VOICES[0]


def test_an_agent_that_speaks_in_the_first_caller_voice_is_called_in_the_second() -> None:
    assert speech.a_callers_voice(speech.CALLER_VOICES[0]) == speech.CALLER_VOICES[1]


# Two sides of one call in one voice is a call nobody listening can follow.
def test_no_caller_voice_is_one_an_agent_is_given_by_name() -> None:
    curated = {voice.voice_id for voice in VOICES.values()}

    assert not curated & {*speech.CALLER_VOICES, speech.A_TELEVISION_VOICE}
