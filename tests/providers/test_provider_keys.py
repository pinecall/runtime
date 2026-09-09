"""Whose key a vendor is built with: the org's own when it brought one, the box's when not."""

import pytest
from livekit.plugins import anthropic, elevenlabs, soniox

from pinecall._settings import Settings
from pinecall.providers.llm import VENDORS as LLM_VENDORS
from pinecall.providers.registry import KEY_OF, NO_ORG_KEYS, Asked, NoProvider, a_key
from pinecall.providers.stt import VENDORS as STT_VENDORS
from pinecall.providers.tts import VENDORS as TTS_VENDORS
from pinecall.types import VENDORS

pytestmark = pytest.mark.unit

THE_BOXES = "the-boxes-own-key"
THE_ORGS = "the-orgs-own-key"


def a_box_that_read_every_key() -> Settings:
    """A process whose environment holds one dead sentinel per vendor, as ring 0 always does."""
    return Settings(
        anthropic_api_key=THE_BOXES,
        openai_api_key=THE_BOXES,
        soniox_api_key=THE_BOXES,
        deepgram_api_key=THE_BOXES,
        eleven_api_key=THE_BOXES,
        whatsapp_access_token=THE_BOXES,
    )


# Criterion 1, at the one line that reads a key.
def test_an_org_that_brought_a_key_for_a_vendor_runs_that_vendor_with_it() -> None:
    asked = Asked(settings=a_box_that_read_every_key(), keys={"elevenlabs": THE_ORGS})
    assert a_key("elevenlabs", asked) == THE_ORGS


def test_an_org_that_brought_none_runs_every_vendor_on_the_box() -> None:
    """Managed is the default and is not a code path: it is the absence of a row."""
    asked = Asked(settings=a_box_that_read_every_key(), keys=NO_ORG_KEYS)
    assert [a_key(vendor, asked) for vendor in VENDORS] == [THE_BOXES] * len(VENDORS)


# WhatsApp is not a model vendor and has no plugin: what it shares with the five is the ONE
# question a door asks before it opens anything — whose key does this call run on.
def test_the_whatsapp_token_is_read_through_the_very_same_question() -> None:
    box = a_box_that_read_every_key()
    assert a_key("whatsapp", Asked(settings=box, keys={"whatsapp": THE_ORGS})) == THE_ORGS
    assert a_key("whatsapp", Asked(settings=box, keys=NO_ORG_KEYS)) == THE_BOXES
    with pytest.raises(NoProvider, match="whatsapp has no API key in this process"):
        a_key("whatsapp", Asked(settings=Settings()))


def test_a_key_the_org_brought_for_one_vendor_is_never_read_for_another() -> None:
    """One row is one vendor: a tenant's ElevenLabs key must not reach Anthropic."""
    asked = Asked(settings=a_box_that_read_every_key(), keys={"elevenlabs": THE_ORGS})
    assert a_key("anthropic", asked) == THE_BOXES


def test_a_vendor_with_neither_key_is_refused_by_name_before_the_call_starts() -> None:
    with pytest.raises(NoProvider, match="soniox has no API key in this process"):
        a_key("soniox", Asked(settings=Settings(soniox_api_key="")))


def test_every_vendor_a_row_may_name_has_the_settings_field_the_box_falls_back_to() -> None:
    """Two tables, one fact read two ways; this is what keeps the domain and providers/ in step."""
    assert tuple(sorted(KEY_OF)) == VENDORS


# Criterion 1, on the built objects: the plugin is handed the key, whichever of the two it was.
def test_the_voice_is_built_with_the_orgs_key_and_without_one_with_the_boxs() -> None:
    theirs = TTS_VENDORS.build(
        "elevenlabs", Asked(settings=a_box_that_read_every_key(), keys={"elevenlabs": THE_ORGS})
    )
    ours = TTS_VENDORS.build("elevenlabs", Asked(settings=a_box_that_read_every_key()))
    assert isinstance(theirs, elevenlabs.TTS) and isinstance(ours, elevenlabs.TTS)
    assert theirs._opts.api_key == THE_ORGS  # pyright: ignore[reportPrivateUsage]
    assert ours._opts.api_key == THE_BOXES  # pyright: ignore[reportPrivateUsage]


def test_the_ears_and_the_model_are_built_the_same_way_from_the_same_row() -> None:
    """One rule for every modality: the vendor file names its vendor and reads nothing else."""
    keys = {"soniox": THE_ORGS, "anthropic": THE_ORGS}
    asked = Asked(settings=a_box_that_read_every_key(), keys=keys)
    ears = STT_VENDORS.build("soniox", asked)
    thinking = LLM_VENDORS.build("anthropic", asked)
    assert isinstance(ears, soniox.STT) and isinstance(thinking, anthropic.LLM)
    assert ears._api_key == THE_ORGS  # pyright: ignore[reportPrivateUsage]
    assert thinking._client.api_key == THE_ORGS  # pyright: ignore[reportPrivateUsage]
