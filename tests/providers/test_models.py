"""The llm modality: the same table as every other, and the vendor a price row is keyed by."""

import pytest
from livekit.agents import llm as agents
from livekit.plugins import anthropic, openai

from pinecall._settings import Settings
from pinecall.providers.llm import VENDORS
from pinecall.providers.llm.anthropic import DEFAULT_MODEL
from pinecall.providers.models import DEFAULT_VENDOR, NoProvider, models_for, vendor_of
from pinecall.providers.registry import NO_ORG_KEYS, Asked
from pinecall.types import Model

pytestmark = pytest.mark.unit

A_KEY = "sk-nobody-will-ever-deploy-this"
# What a tenant who has their own account with the vendor put in the vault. The whole of BYOK is
# that this string, and never A_KEY, is the one the plugin was built with.
THE_ORGS_OWN = "sk-the-clinics-own-account"


def settings() -> Settings:
    """A process that read a key for both vendors of the table."""
    return Settings(anthropic_api_key=A_KEY, openai_api_key=A_KEY)


@pytest.mark.parametrize("vendor", VENDORS.names)
def test_every_file_of_the_modality_builds_a_livekit_llm(vendor: str) -> None:
    """Criterion 1: the plugin IS the adapter, so a vendor hands back the library's own class."""
    built = VENDORS.build(vendor, Asked(settings=settings(), model="a-model"))
    assert isinstance(built, agents.LLM)
    assert built.model == "a-model"


def test_the_anthropic_file_turns_prompt_caching_on() -> None:
    """The static prompt region is what a cache is for; it is the only option a vendor sets."""
    built = models_for(settings())(Model(provider="anthropic", model=DEFAULT_MODEL), NO_ORG_KEYS)
    assert isinstance(built, anthropic.LLM)
    assert built._opts.caching == "ephemeral"  # pyright: ignore[reportPrivateUsage]


def test_the_openai_file_is_the_plugin_and_nothing_of_ours() -> None:
    built = models_for(settings())(Model(provider="openai", model="gpt-5-mini"), NO_ORG_KEYS)
    assert isinstance(built, openai.LLM)


def test_no_model_at_all_is_the_one_default_this_build_states() -> None:
    """The default vendor is named here; which of its models runs is that vendor file's business."""
    built = models_for(settings())(None, NO_ORG_KEYS)
    assert vendor_of(built) == DEFAULT_VENDOR == "anthropic"
    assert built.model == DEFAULT_MODEL


def test_a_provider_with_no_file_is_refused_by_name() -> None:
    with pytest.raises(NoProvider, match="no llm vendor named 'mistral'"):
        models_for(settings())(Model(provider="mistral", model="whatever"), NO_ORG_KEYS)


# Criterion 1 at the seam every door goes through: managed is the absence of a row, BYOK is one
# row, and which of the two a call got is invisible above this function — provider-keys.md.
def test_the_org_that_brought_its_own_key_is_the_one_the_model_is_built_with() -> None:
    """A written call of a BYOK org reaches the vendor on the org's account, not on the box's."""
    asked = Model(provider="anthropic", model=DEFAULT_MODEL)
    built = models_for(settings())(asked, {"anthropic": THE_ORGS_OWN})
    assert isinstance(built, anthropic.LLM)
    assert built._client.api_key == THE_ORGS_OWN  # pyright: ignore[reportPrivateUsage]


def test_an_org_that_brought_a_key_for_another_vendor_still_runs_on_the_boxs() -> None:
    """One row is one vendor: a clinic's ElevenLabs key says nothing about who pays for the LLM."""
    asked = Model(provider="anthropic", model=DEFAULT_MODEL)
    built = models_for(settings())(asked, {"elevenlabs": THE_ORGS_OWN})
    assert isinstance(built, anthropic.LLM)
    assert built._client.api_key == A_KEY  # pyright: ignore[reportPrivateUsage]


def test_a_provider_with_no_key_is_refused_now_and_not_mid_call() -> None:
    with pytest.raises(NoProvider, match="anthropic has no API key"):
        keyless = Settings(anthropic_api_key="", openai_api_key="")
        models_for(keyless)(Model(provider="anthropic", model=DEFAULT_MODEL), NO_ORG_KEYS)


# livekit's own LLM.provider is the API host the client points at, which is the right answer for a
# trace and the wrong one for a price table keyed by vendor.
def test_the_vendor_a_price_row_uses_is_the_plugin_not_the_host() -> None:
    built = models_for(settings())(Model(provider="anthropic", model=DEFAULT_MODEL), NO_ORG_KEYS)
    assert "anthropic" in built.provider  # the host, api.anthropic.com
    assert vendor_of(built) == "anthropic"


@pytest.mark.parametrize("vendor", VENDORS.names)
def test_every_installed_plugin_names_its_vendor_in_its_label(vendor: str) -> None:
    """The vendor is read off the plugin's own public label, one file at a time."""
    built = VENDORS.build(vendor, Asked(settings=settings(), model="a-model"))
    assert built.label == f"livekit.plugins.{vendor}.llm.LLM"
    assert vendor_of(built) == vendor
