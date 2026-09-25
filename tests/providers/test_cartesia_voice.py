"""Cartesia's voice: sonic-3 when nobody chose, and the agent's language sent, not the plugin's."""

import pytest
from livekit.plugins import cartesia

from pinecall._settings import Settings
from pinecall.providers.registry import Asked
from pinecall.providers.tts import VENDORS
from pinecall.providers.tts.cartesia import DEFAULT_MODEL, VOICE_FOR

pytestmark = pytest.mark.unit

A_KEY = "nobody-will-ever-deploy-this"
MARTA = "de38f545-c574-44e8-9b54-a7d6fec1c6b1"


def built(
    voice_id: str | None = None, language: str | None = None, model: str | None = None
) -> cartesia.TTS:
    asked = Asked(
        settings=Settings(world="production", cartesia_api_key=A_KEY),
        voice_id=voice_id,
        language=language,
        model=model,
    )
    speaks = VENDORS.build("cartesia", asked)
    assert isinstance(speaks, cartesia.TTS)
    return speaks


def test_a_build_that_named_no_model_speaks_sonic_3() -> None:
    assert built()._opts.model == DEFAULT_MODEL == "sonic-3"  # pyright: ignore[reportPrivateUsage]


def test_a_spanish_agent_is_read_in_spanish_in_the_voice_it_chose() -> None:
    speaks = built(voice_id=MARTA, language="es", model="sonic-2")
    options = speaks._opts  # pyright: ignore[reportPrivateUsage]
    assert (options.voice, options.language, options.model) == (MARTA, "es", "sonic-2")


def test_the_models_this_build_vouches_for_are_offered_the_default_first() -> None:
    assert VENDORS.models("cartesia") == ("sonic-3", "sonic-2")


def test_an_agent_that_chose_no_voice_is_read_by_a_native_speaker_of_its_language() -> None:
    assert built(language="es")._opts.voice == VOICE_FOR["es"]  # pyright: ignore[reportPrivateUsage]
    assert built(language="en")._opts.voice == VOICE_FOR["en"]  # pyright: ignore[reportPrivateUsage]
    assert built(language="fr")._opts.voice == VOICE_FOR["en"]  # pyright: ignore[reportPrivateUsage]
