"""The llm modality: the same table as every other, and the vendor a price row is keyed by."""

import pytest
from livekit.agents import llm as agents
from livekit.plugins import anthropic, openai

from pinecall.providers.llm import VENDORS
from pinecall.providers.llm.anthropic import DEFAULT_MODEL
from pinecall.providers.models import DEFAULT_VENDOR, NoProvider, models_for, vendor_of
from pinecall.providers.registry import Asked
from pinecall.settings import Settings
from pinecall.types import NOTHING_BROUGHT, Brought, Model
from tests.plugins import without_the_plugin

pytestmark = pytest.mark.unit

A_KEY = "sk-nobody-will-ever-deploy-this"
# What a tenant who has their own account with the vendor put in the vault. The whole of BYOK is
# that this string, and never A_KEY, is the one the plugin was built with.
THE_ORGS_OWN = "sk-the-clinics-own-account"


# Long enough that PyJWT does not warn about the HMAC key, because the suite turns a warning into
# an error and a LiveKit secret really is signed with (livekit/api/access_token.py, to_jwt).
A_LIVEKIT_SECRET = "dead-sentinel-dead-sentinel-dead-sentinel"


def settings() -> Settings:
    """A box with a key for both tuned vendors of the table, and a LiveKit project of its own."""
    return Settings(
        world="production",
        anthropic_api_key=A_KEY,
        openai_api_key=A_KEY,
        livekit_api_key=A_KEY,
        livekit_api_secret=A_LIVEKIT_SECRET,
    )


def a_box_with_no_livekit_project() -> Settings:
    """A box that has vendor keys and no LiveKit pair: Inference is the one vendor it cannot run."""
    return Settings(
        world="production", anthropic_api_key=A_KEY, livekit_api_key=None, livekit_api_secret=None
    )


@pytest.mark.parametrize("vendor", VENDORS.tuned)
def test_every_file_of_the_modality_builds_a_livekit_llm(vendor: str) -> None:
    """Criterion 1: the plugin IS the adapter, so a vendor hands back the library's own class."""
    built = VENDORS.build(vendor, Asked(settings=settings(), model="a-model"))
    assert isinstance(built, agents.LLM)
    assert built.model == "a-model"


def test_the_anthropic_file_turns_prompt_caching_on() -> None:
    """The static prompt region is what a cache is for; it is the only option a vendor sets."""
    built = models_for(settings())(
        Model(provider="anthropic", model=DEFAULT_MODEL), NOTHING_BROUGHT
    )
    assert isinstance(built, anthropic.LLM)
    assert built._opts.caching == "ephemeral"  # pyright: ignore[reportPrivateUsage]


def test_the_openai_file_is_the_plugin_and_nothing_of_ours() -> None:
    built = models_for(settings())(Model(provider="openai", model="gpt-5-mini"), NOTHING_BROUGHT)
    assert isinstance(built, openai.LLM)


def test_no_model_at_all_is_the_one_default_this_build_states() -> None:
    """The default vendor is named here; which of its models runs is that vendor file's business."""
    built = models_for(settings())(None, NOTHING_BROUGHT)
    assert vendor_of(built) == DEFAULT_VENDOR == "anthropic"
    assert built.model == DEFAULT_MODEL


def test_a_word_nobody_catalogues_is_refused_by_name() -> None:
    with pytest.raises(NoProvider, match="no llm vendor named 'zenith'"):
        models_for(settings())(Model(provider="zenith", model="whatever"), NOTHING_BROUGHT)


# The catalogued half of the table: a vendor with no file here is not a vendor this build refuses,
# it is one it has no plugin installed for — and the refusal says which extra installs it.
@without_the_plugin("google")
def test_a_catalogued_vendor_with_no_plugin_names_the_extra_that_installs_it() -> None:
    """Google is catalogued and is in the `providers-big` extra, which a light box does not hold."""
    with pytest.raises(NoProvider, match=r"livekit-agents\[google\]"):
        models_for(settings())(Model(provider="gemini", model="gemini-3-flash"), NOTHING_BROUGHT)


# The alias table, at the one door a declaration comes through: providers/declaration.py writes the
# canonical name into the config, and the registry resolves one again for anything that skipped it.
def test_a_vendor_asked_for_by_an_alias_is_the_same_vendor() -> None:
    built = models_for(settings())(Model(provider="claude", model=DEFAULT_MODEL), NOTHING_BROUGHT)
    assert isinstance(built, anthropic.LLM)
    assert vendor_of(built) == "anthropic"


# Criterion 1 at the seam every door goes through: managed is the absence of a row, BYOK is one
# row, and which of the two a call got is invisible above this function — provider-keys.md.
def test_the_org_that_brought_its_own_key_is_the_one_the_model_is_built_with() -> None:
    """A written call of a BYOK org reaches the vendor on the org's account, not on the box's."""
    asked = Model(provider="anthropic", model=DEFAULT_MODEL)
    built = models_for(settings())(asked, Brought(keys={"anthropic": THE_ORGS_OWN}))
    assert isinstance(built, anthropic.LLM)
    assert built._client.api_key == THE_ORGS_OWN  # pyright: ignore[reportPrivateUsage]


def test_an_org_that_brought_a_key_for_another_vendor_still_runs_on_the_boxs() -> None:
    """One row is one vendor: a clinic's ElevenLabs key says nothing about who pays for the LLM."""
    asked = Model(provider="anthropic", model=DEFAULT_MODEL)
    built = models_for(settings())(asked, Brought(keys={"elevenlabs": THE_ORGS_OWN}))
    assert isinstance(built, anthropic.LLM)
    assert built._client.api_key == A_KEY  # pyright: ignore[reportPrivateUsage]


def test_a_provider_with_no_key_is_refused_now_and_not_mid_call() -> None:
    with pytest.raises(NoProvider, match="anthropic has no API key"):
        keyless = Settings(world="production", anthropic_api_key="", openai_api_key="")
        models_for(keyless)(Model(provider="anthropic", model=DEFAULT_MODEL), NOTHING_BROUGHT)


# livekit's own LLM.provider is the API host the client points at, which is the right answer for a
# trace and the wrong one for a price table keyed by vendor.
def test_the_vendor_a_price_row_uses_is_the_plugin_not_the_host() -> None:
    built = models_for(settings())(
        Model(provider="anthropic", model=DEFAULT_MODEL), NOTHING_BROUGHT
    )
    assert "anthropic" in built.provider  # the host, api.anthropic.com
    assert vendor_of(built) == "anthropic"


@pytest.mark.parametrize("vendor", [name for name in VENDORS.tuned if name != "livekit"])
def test_every_installed_plugin_names_its_vendor_in_its_label(vendor: str) -> None:
    """The vendor is read off the plugin's own public label, one file at a time."""
    built = VENDORS.build(vendor, Asked(settings=settings(), model="a-model"))
    assert built.label == f"livekit.plugins.{vendor}.llm.LLM"
    assert vendor_of(built) == vendor


# Inference is the one vendor whose label carries no vendor of its own — it is livekit's gateway,
# and the vendor is written inside the model name. Reading the label by position is what keeps it
# from answering `livekit` for every plugin, since every plugin's label begins with that word.
def test_livekit_inference_is_its_own_vendor_and_steals_nobody_elses_label() -> None:
    inference = VENDORS.build("livekit", Asked(settings=settings()))
    assert inference.label == "livekit.agents.inference.llm.LLM"
    assert vendor_of(inference) == "livekit"
    plugin = VENDORS.build("openai", Asked(settings=settings(), model="a-model"))
    assert vendor_of(plugin) == "openai"


def test_livekit_inference_is_refused_without_the_boxes_own_pair() -> None:
    """It bills the box's LiveKit project, so the project's key and secret are its key."""
    with pytest.raises(NoProvider, match="LIVEKIT_API_KEY"):
        VENDORS.build("livekit", Asked(settings=a_box_with_no_livekit_project()))


def test_a_written_call_is_refused_a_model_the_box_does_not_lend_the_org() -> None:
    """The text path builds its model through the same table, so the same lending binds it."""
    trial = Brought(lends=frozenset({"anthropic/claude-haiku-4-5"}))
    assert isinstance(
        models_for(settings())(Model(provider="anthropic", model=DEFAULT_MODEL), trial),
        anthropic.LLM,
    )
    with pytest.raises(NoProvider, match="not lent"):
        models_for(settings())(Model(provider="anthropic", model="claude-opus-5"), trial)
