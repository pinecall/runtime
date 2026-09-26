"""The catalog against the plugins it claims: a row that disagrees with livekit is a bug here."""

from importlib import import_module

import pytest

from pinecall.providers import catalog
from pinecall.providers.catalog import MODALITIES, PROVIDERS, Modality, Provider
from pinecall.providers.plugin import CLASS_OF, installed

pytestmark = pytest.mark.unit

# Two plugins this build installs do not import at all, and it is livekit's packaging and not ours:
# upliftai's wheel forgets python-socketio, and clova's pulls pydub, which imports the `audioop`
# module Python 3.12 deprecated and this suite turns into an error. They stay in the catalog
# because livekit ships them and the day either is fixed the row is already right — and a call that
# asks for one is refused by name with the importer's own sentence, which is the test below.
WILL_NOT_IMPORT: dict[str, Modality] = {"clova": "stt", "upliftai": "tts"}

# The vendors with a plugin this build actually has. A row for one nobody installed is not checked
# here — there is nothing to check it against — and providers/plugin.py refuses it by name.
INSTALLED = [
    row for row in PROVIDERS if row.plugin and installed(row) and row.name not in WILL_NOT_IMPORT
]


def test_the_catalogue_is_forty_odd_vendors_and_not_five() -> None:
    """The number is the point of the file: the five with a tuned file are the exception now."""
    assert len(catalog.doing("llm")) > 8
    assert len(catalog.doing("stt")) > 20
    assert len(catalog.doing("tts")) > 30


@pytest.mark.parametrize("row", INSTALLED, ids=lambda row: row.name)
def test_a_row_claims_exactly_what_its_plugin_exports(row: Provider) -> None:
    """`does` is read off the plugin's own __all__, so the two are checked against each other."""
    plugin = import_module(f"livekit.plugins.{row.plugin}")
    exports = {job for job in MODALITIES if hasattr(plugin, CLASS_OF[job])}
    assert exports == set(row.does), (
        f"{row.name}: the catalog says {set(row.does)}, it has {exports}"
    )


@pytest.mark.parametrize(("vendor", "modality"), WILL_NOT_IMPORT.items())
def test_a_plugin_that_cannot_import_is_a_refusal_and_never_a_traceback(
    vendor: str, modality: Modality
) -> None:
    """A broken wheel reads as a line on a screen, not as a job that died with a caller in it."""
    from pinecall._settings import Settings
    from pinecall.providers.registry import Asked, NoProvider
    from pinecall.providers.stt import VENDORS as STT_VENDORS
    from pinecall.providers.tts import VENDORS as TTS_VENDORS

    field = catalog.settings_field_of(vendor)
    assert field is not None
    box = Settings.model_construct(None, **{field: "dead-sentinel"})
    vendors = STT_VENDORS if modality == "stt" else TTS_VENDORS
    with pytest.raises(NoProvider, match="does not import"):
        vendors.build(vendor, Asked(settings=box))


def test_every_word_a_person_writes_reaches_one_vendor() -> None:
    """A name, an alias, a capital, a stray space: the same vendor out of every one of them."""
    assert catalog.canonical("11labs") == "elevenlabs"
    assert catalog.canonical(" Claude ") == "anthropic"
    assert catalog.canonical("GEMINI") == "google"
    assert catalog.canonical("inference") == "livekit"


def test_a_word_nobody_catalogues_comes_back_as_it_was_typed() -> None:
    """This function never invents a vendor: the door that refuses one says so in its own words."""
    assert catalog.canonical("zenith") == "zenith"
    assert catalog.named("zenith") is None


def test_resolving_a_name_twice_is_resolving_it_once() -> None:
    for row in PROVIDERS:
        for word in (row.name, *row.aliases):
            assert catalog.canonical(catalog.canonical(word)) == row.name


@pytest.mark.parametrize("modality", MODALITIES)
def test_the_vendor_this_build_runs_by_default_is_one_of_the_rows(modality: Modality) -> None:
    """A default nobody catalogued would be a pipeline that refuses itself before the first call."""
    from pinecall.providers.models import DEFAULT_VENDOR
    from pinecall.providers.session_vendors import DEFAULT_STT, DEFAULT_TTS

    ours = {"llm": DEFAULT_VENDOR, "stt": DEFAULT_STT, "tts": DEFAULT_TTS}[modality]
    assert ours in {row.name for row in catalog.doing(modality)}


def test_whatsapp_is_a_key_and_never_a_pipeline() -> None:
    """It is in the table so BYOK reads one list; nothing builds a session out of it."""
    assert catalog.named("whatsapp") is not None
    assert "whatsapp" in catalog.vendors_with_a_key()
    assert not any(row.name == "whatsapp" for job in MODALITIES for row in catalog.doing(job))
