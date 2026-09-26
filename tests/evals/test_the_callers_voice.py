"""The caller's voice: its own when it declared one, never the agent's, at the room's rate."""

import pytest

from pinecall._settings import Settings
from pinecall.evals import caller_voice
from pinecall.providers.registry import Asked
from pinecall.providers.tts.cartesia import VOICE_FOR
from pinecall.providers.tts.curated_voices import VOICES
from pinecall.types import Voice as DeclaredVoice

pytestmark = pytest.mark.unit

# One second of silence, 16-bit mono, at a rate the vendor might answer at.
A_SECOND_AT = {rate: b"\x00\x00" * rate for rate in (22_050, caller_voice.SAMPLE_RATE)}


def test_audio_already_at_the_rooms_rate_comes_back_untouched() -> None:
    pcm = caller_voice.at_the_rooms_rate(
        A_SECOND_AT[caller_voice.SAMPLE_RATE], caller_voice.SAMPLE_RATE
    )

    assert pcm == A_SECOND_AT[caller_voice.SAMPLE_RATE]


# The caller's track is opened at the room's rate before a word exists, so what the vendor sent is
# brought to that rate, and a second of speech is still a second of speech when it is pushed.
def test_audio_at_the_vendors_rate_comes_back_as_a_second_at_the_rooms() -> None:
    pcm = caller_voice.at_the_rooms_rate(A_SECOND_AT[22_050], 22_050)

    samples = len(pcm) // 2
    assert abs(samples - caller_voice.SAMPLE_RATE) < caller_voice.SAMPLE_RATE // 100, (
        "a second in should be a second out, within the resampler's own edge"
    )


SPAIN = caller_voice.CALLER_VOICES["es"]
ENGLISH = caller_voice.CALLER_VOICES["en"]


def test_the_caller_speaks_in_the_first_caller_voice_when_the_agent_has_another() -> None:
    assert caller_voice.a_callers_voice(VOICE_FOR["es"], "es") == SPAIN[0]


def test_an_agent_that_speaks_in_the_first_caller_voice_is_called_in_the_second() -> None:
    assert caller_voice.a_callers_voice(SPAIN[0], "es-ES") == SPAIN[1]


def test_a_language_with_no_pair_of_its_own_is_called_in_englishs() -> None:
    assert caller_voice.a_callers_voice(None, "fr") == ENGLISH[0]
    assert caller_voice.a_callers_voice(None) == ENGLISH[0]


# Two sides of one call in one voice is a call nobody listening can follow.
def test_no_caller_voice_is_one_an_agent_is_given() -> None:
    given = {voice.voice_id for voice in VOICES.values()} | set(VOICE_FOR.values())
    callers = {voice for pair in caller_voice.CALLER_VOICES.values() for voice in pair}

    assert not given & {*callers, caller_voice.A_TELEVISION_VOICE}


# A persona that declared a voice speaks in it — its vendor, its model, its id — whatever the
# agent speaks in; one that declared none gets the premade the agent does not have, as before.
def test_a_persona_that_declared_its_voice_speaks_in_it(monkeypatch: pytest.MonkeyPatch) -> None:
    built = _the_builds(monkeypatch)
    declared = DeclaredVoice(provider="cartesia", model="sonic-3", voice_id="a-uuid")

    caller_voice.Voice.of_the_caller(
        Settings(world="production"), caller_voice.Speaking(language="es", declared=declared)
    )

    [(vendor, asked)] = built
    assert (vendor, asked.model, asked.voice_id, asked.language) == (
        "cartesia",
        "sonic-3",
        "a-uuid",
        "es",
    )


def test_a_persona_that_declared_none_speaks_in_a_voice_the_agent_does_not_have(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built = _the_builds(monkeypatch)

    speaking = caller_voice.Speaking(language="es", agents_voice=SPAIN[0])
    caller_voice.Voice.of_the_caller(Settings(world="production"), speaking)

    [(vendor, asked)] = built
    assert (vendor, asked.voice_id) == ("cartesia", SPAIN[1])


def _the_builds(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Asked]]:
    """Every voice the caller asked the vendor table for, and nothing reaching a vendor."""
    built: list[tuple[str, Asked]] = []

    def build(vendor: str, asked: Asked) -> object:
        built.append((vendor, asked))
        return object()

    monkeypatch.setattr(caller_voice.tts.VENDORS, "build", build)
    return built
