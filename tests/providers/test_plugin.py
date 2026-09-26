"""The generic half of the table: a vendor with no file here, built out of its own plugin."""

import pytest
from livekit.plugins import cartesia, elevenlabs, rime

from pinecall._settings import Settings
from pinecall.providers import catalog
from pinecall.providers.registry import Asked, NoProvider
from pinecall.providers.stt import VENDORS as STT_VENDORS
from pinecall.providers.tts import VENDORS as TTS_VENDORS

pytestmark = pytest.mark.unit

A_KEY = "nobody-will-ever-deploy-this"
A_VOICE = "694f9389-aac1-45b6-b726-9d9369183238"


def a_box() -> Settings:
    """One dead sentinel under every vendor's own variable, as ring 0 always has."""
    held = dict.fromkeys(map(catalog.settings_field_of, catalog.vendors_with_a_key()), A_KEY)
    return Settings.model_construct(None, **{field: A_KEY for field in held if field})


# The reason this file exists instead of forty vendor files: every plugin spells a voice
# differently, nothing here writes those names down, and the signature is read at the moment one is
# built. Three plugins, three spellings, one function (providers/plugin.py).
def test_the_voice_lands_under_whatever_name_each_plugin_gave_it() -> None:
    asked = Asked(settings=a_box(), voice_id=A_VOICE, language="es")
    speaks = TTS_VENDORS.build("cartesia", asked)
    assert isinstance(speaks, cartesia.TTS)
    assert speaks._opts.voice == A_VOICE  # pyright: ignore[reportPrivateUsage]

    speaks = TTS_VENDORS.build("rime", asked)  # `speaker`, not `voice`
    assert isinstance(speaks, rime.TTS)
    assert speaks._opts.speaker == A_VOICE  # pyright: ignore[reportPrivateUsage]

    speaks = TTS_VENDORS.build("elevenlabs", asked)  # `voice_id`, and a tuned file at that
    assert isinstance(speaks, elevenlabs.TTS)
    assert speaks._opts.voice_id == A_VOICE  # pyright: ignore[reportPrivateUsage]


def test_a_plugin_that_names_no_voice_is_simply_not_sent_one() -> None:
    """Deepgram's TTS carries its voice inside the model id, so there is no argument to fill."""
    speaks = TTS_VENDORS.build("deepgram", Asked(settings=a_box(), model="aura-2-thalia-en"))
    assert speaks.label == "livekit.plugins.deepgram.tts.TTS"


def test_the_language_reaches_the_ears_of_a_vendor_with_no_file_here() -> None:
    hears = STT_VENDORS.build("cartesia", Asked(settings=a_box(), language="es"))
    assert isinstance(hears, cartesia.STT)
    assert hears._language == "es"  # pyright: ignore[reportPrivateUsage]


# Several vendors want more than a key — an endpoint, a client id and a secret, a model. They are
# refused BEFORE the call in their own words, which is the whole promise of building the pipeline
# up front: a ValueError out of here would end the job with a caller already in the room.
@pytest.mark.parametrize("vendor", ["baseten", "rtzr", "slng"])
def test_a_vendor_that_wants_more_than_a_key_says_so_as_a_refusal(vendor: str) -> None:
    with pytest.raises(NoProvider, match=f"{vendor} would not build its stt"):
        STT_VENDORS.build(vendor, Asked(settings=a_box()))


def test_a_catalogued_vendor_with_no_plugin_names_the_one_command_that_fixes_it() -> None:
    with pytest.raises(NoProvider, match=r'pip install "livekit-agents\[azure\]"'):
        STT_VENDORS.build("azure", Asked(settings=a_box()))
