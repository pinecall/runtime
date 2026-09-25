"""EMBED_PROVIDER, switched on in one place: which class, which model, which door, whose key."""

from __future__ import annotations

import httpx
import pytest

from pinecall._settings import load_settings
from pinecall.providers.embed import (
    DEFAULTS,
    base_url_of,
    embedder_for,
    key_field_of,
    model_of,
    vendor_of,
)
from pinecall.providers.embed.perplexity import PerplexityEmbedder
from pinecall.providers.embed.tei import TeiEmbedder
from pinecall.providers.registry import NoProvider

pytestmark = pytest.mark.unit


def a_client() -> httpx.AsyncClient:
    """The one client a process opens; nothing here ever sends a request through it."""
    return httpx.AsyncClient()


def test_a_box_that_names_nothing_embeds_with_tei_at_its_older_variable() -> None:
    settings = load_settings()
    assert settings.embed_provider == "tei"
    assert isinstance(embedder_for(settings, a_client()), TeiEmbedder)
    assert model_of(settings) == "BAAI/bge-m3"
    assert base_url_of(settings) == "http://127.0.0.1:8081"
    assert key_field_of(settings) is None


def test_perplexity_defaults_to_the_contextual_model_at_its_own_door(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EMBED_PROVIDER", "perplexity")
    settings = load_settings()
    embedder = embedder_for(settings, a_client())
    assert isinstance(embedder, PerplexityEmbedder)
    assert embedder.reads_the_neighbours
    assert model_of(settings) == "pplx-embed-context-v1-4b"
    assert base_url_of(settings) == "https://api.perplexity.ai/v1"
    assert vendor_of(settings) == "Perplexity"


def test_openrouter_defaults_to_the_flat_model_because_it_serves_no_contextual_door(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EMBED_PROVIDER", "openrouter")
    settings = load_settings()
    embedder = embedder_for(settings, a_client())
    assert isinstance(embedder, PerplexityEmbedder)
    assert not embedder.reads_the_neighbours
    assert model_of(settings) == "perplexity/pplx-embed-v1-0.6b"
    assert base_url_of(settings) == "https://openrouter.ai/api/v1"
    assert vendor_of(settings) == "OpenRouter"


def test_the_environment_may_name_the_model_and_the_door_over_the_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Naming the FLAT model on Perplexity is how a box asks for the cheaper of the two."""
    monkeypatch.setenv("EMBED_PROVIDER", "perplexity")
    monkeypatch.setenv("EMBED_MODEL", "pplx-embed-v1-0.6b")
    monkeypatch.setenv("EMBED_BASE_URL", "https://proxy.test/v1")
    settings = load_settings()
    embedder = embedder_for(settings, a_client())
    assert isinstance(embedder, PerplexityEmbedder)
    assert not embedder.reads_the_neighbours
    assert base_url_of(settings) == "https://proxy.test/v1"


def test_a_hosted_provider_with_no_key_is_refused_by_the_variable_to_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EMBED_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    with pytest.raises(NoProvider, match="set OPENROUTER_API_KEY"):
        embedder_for(load_settings(), a_client())


def test_each_provider_asks_the_flat_door_for_the_encoding_it_actually_takes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Measured against both: api.perplexity.ai refuses `float`, OpenRouter answers it."""
    assert DEFAULTS["perplexity"].encoding == "base64_int8"
    assert DEFAULTS["openrouter"].encoding == "float"
    monkeypatch.setenv("EMBED_PROVIDER", "perplexity")
    monkeypatch.setenv("EMBED_MODEL", "pplx-embed-v1-0.6b")
    embedder = embedder_for(load_settings(), a_client())
    assert isinstance(embedder, PerplexityEmbedder)
