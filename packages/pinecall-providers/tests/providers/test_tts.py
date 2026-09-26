"""The voice, and the trap: the plugin's own default is a model this repo will not run."""

import logging

import pytest
from livekit.plugins import elevenlabs

from pinecall.providers.registry import Asked, NoProvider
from pinecall.providers.tts import VENDORS
from pinecall.providers.tts.elevenlabs import ALLOWED, DEFAULT_MODEL, INSTEAD
from pinecall.settings import Settings

pytestmark = pytest.mark.unit

A_KEY = "nobody-will-ever-deploy-this"

# What the plugin's own signature defaults to (livekit-session.md #16). It must never come out of a
# build of ours, whatever an agent asked for and whatever it forgot to ask for.
THE_FORBIDDEN_DEFAULT = "eleven_turbo_v2_5"


def an_ask(**asked: object) -> Asked:
    """A process that read the ElevenLabs key."""
    settings = Settings(world="production", eleven_api_key=A_KEY)
    return Asked(settings=settings, **asked)  # pyright: ignore[reportArgumentType]


def a_model_of(built: object) -> str:
    """The model a built TTS will actually speak with, read off the plugin's own options."""
    assert isinstance(built, elevenlabs.TTS)
    return str(built._opts.model)  # pyright: ignore[reportPrivateUsage]


# Criterion 2, first half: an agent that declares no model gets ours, never the plugin's.
def test_a_build_given_no_model_does_not_fall_through_to_the_plugins_turbo() -> None:
    built = VENDORS.build("elevenlabs", an_ask())
    assert a_model_of(built) == DEFAULT_MODEL == "eleven_flash_v2_5"
    assert a_model_of(built) != THE_FORBIDDEN_DEFAULT


# Criterion 2, second half: asking for turbo by name yields flash, and the log says why.
def test_asking_for_turbo_speaks_with_flash_and_warns_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="pinecall.providers.tts.elevenlabs"):
        built = VENDORS.build("elevenlabs", an_ask(model=THE_FORBIDDEN_DEFAULT))
    assert a_model_of(built) == DEFAULT_MODEL
    assert len(caplog.records) == 1
    assert THE_FORBIDDEN_DEFAULT in caplog.text and DEFAULT_MODEL in caplog.text


def test_eleven_v3_speaks_with_the_conversational_one_it_should_have_asked_for(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="pinecall.providers.tts.elevenlabs"):
        built = VENDORS.build("elevenlabs", an_ask(model="eleven_v3"))
    assert a_model_of(built) == "eleven_v3_conversational"
    assert len(caplog.records) == 1


def test_neither_forbidden_model_can_come_out_of_a_build() -> None:
    """The whole point, stated once: no path through this file returns what the rules forbid."""
    assert set(INSTEAD) == {THE_FORBIDDEN_DEFAULT, "eleven_v3"}
    assert not set(INSTEAD) & ALLOWED
    for wanted in (None, *INSTEAD, *ALLOWED):
        built = VENDORS.build("elevenlabs", an_ask(model=wanted))
        assert a_model_of(built) not in INSTEAD


def test_a_model_nobody_has_heard_of_is_refused_with_the_ones_we_run() -> None:
    with pytest.raises(NoProvider, match="no elevenlabs model 'eleven_flash_v9'"):
        VENDORS.build("elevenlabs", an_ask(model="eleven_flash_v9"))


def test_the_voice_and_the_alignment_are_what_the_agent_asked_for() -> None:
    """sync_alignment is what a tts.word entry is made of, and the plugin has it on (tts.py:124)."""
    built = VENDORS.build("elevenlabs", an_ask(voice_id="a-voice", language="es"))
    assert isinstance(built, elevenlabs.TTS)
    options = built._opts  # pyright: ignore[reportPrivateUsage]
    assert options.voice_id == "a-voice"
    assert options.language == "es"
    assert options.sync_alignment is True


def test_an_undeclared_voice_is_the_plugins_own_and_is_said_out_loud() -> None:
    built = VENDORS.build("elevenlabs", an_ask())
    assert isinstance(built, elevenlabs.TTS)
    assert built._opts.voice_id == elevenlabs.DEFAULT_VOICE_ID  # pyright: ignore[reportPrivateUsage]


def test_a_vendor_with_no_key_is_refused_at_the_door_of_the_call() -> None:
    with pytest.raises(NoProvider, match="elevenlabs has no API key"):
        VENDORS.build("elevenlabs", Asked(settings=Settings(world="production", eleven_api_key="")))
