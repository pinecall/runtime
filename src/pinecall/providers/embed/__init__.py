"""Who embeds: one file per vendor, and the one place a provider's name is ever switched on."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from pinecall._settings import EmbedProvider, Settings, variable_of
from pinecall.providers.embed.perplexity import PerplexityEmbedder
from pinecall.providers.embed.tei import TeiEmbedder
from pinecall.providers.embed.wire import FLOATS, SIGNED_BYTES
from pinecall.providers.embedder import Embedder
from pinecall.providers.registry import NoProvider

# A vendor with no key is a refusal at startup, and never a 500 in the middle of a push.
NO_KEY = "the {provider} embedder has no API key in this process: set {variable}"


@dataclass(frozen=True)
class Defaults:
    """One provider as the environment need not spell it: its name, its model, its door, its key."""

    vendor: str
    model: str
    base_url: str
    # The settings field its key is read from, or None for a service on the box, which takes none.
    # Not registry.py's KEY_OF: that table is the per-call vendors an ORG may bring its own key
    # for, and the embedder is the box's own — one gateway, one embedder, one key.
    key_field: str | None = None
    # What the FLAT door of this vendor accepts, measured against both: api.perplexity.ai refuses
    # `float` and takes only base64, OpenRouter's mirror of the same model answers floats. The
    # contextual door has one encoding and takes it from nobody.
    encoding: str = SIGNED_BYTES


# TEI's base URL is TEI_URL, which a box already sets and a compose file already serves, so its
# row leaves it empty and `base_url_of` reads the older name. Perplexity's default is the
# CONTEXTUAL model, and its larger one — the 4b, cut to the columns' 1024 — because a base pushed
# with it is the better base; OpenRouter serves the flat
# model only — it answers `does not exist` at the contextual door — so its default is the flat one.
DEFAULTS: dict[EmbedProvider, Defaults] = {
    "tei": Defaults(vendor="TEI", model="BAAI/bge-m3", base_url=""),
    "perplexity": Defaults(
        vendor="Perplexity",
        model="pplx-embed-context-v1-4b",
        base_url="https://api.perplexity.ai/v1",
        key_field="perplexity_api_key",
    ),
    "openrouter": Defaults(
        vendor="OpenRouter",
        model="perplexity/pplx-embed-v1-0.6b",
        base_url="https://openrouter.ai/api/v1",
        key_field="openrouter_api_key",
        encoding=FLOATS,
    ),
}


# The ONLY place that switches on EMBED_PROVIDER. Everything above holds an Embedder and never
# learns whose it is; the doctor asks the four readers below what this box is configured with.
def embedder_for(settings: Settings, http: httpx.AsyncClient) -> Embedder:
    """The embedder this box runs, over the one http client the process opened."""
    if settings.embed_provider == "tei":
        return TeiEmbedder(base_url_of(settings), http)
    return PerplexityEmbedder(
        vendor=vendor_of(settings),
        base_url=base_url_of(settings),
        model=model_of(settings),
        key=a_key(settings),
        http=http,
        encoding=DEFAULTS[settings.embed_provider].encoding,
    )


def model_of(settings: Settings) -> str:
    """What this box embeds with: EMBED_MODEL, or the provider's own default."""
    return settings.embed_model or DEFAULTS[settings.embed_provider].model


def base_url_of(settings: Settings) -> str:
    """Where it is asked: EMBED_BASE_URL, the provider's own door, or TEI's older TEI_URL."""
    if settings.embed_base_url:
        return settings.embed_base_url
    if settings.embed_provider == "tei":
        return settings.tei_url
    return DEFAULTS[settings.embed_provider].base_url


def vendor_of(settings: Settings) -> str:
    """The vendor's name as a refusal and the doctor's line spell it."""
    return DEFAULTS[settings.embed_provider].vendor


def key_field_of(settings: Settings) -> str | None:
    """Which settings field holds this provider's key; None for TEI, which is asked for none."""
    return DEFAULTS[settings.embed_provider].key_field


def a_key(settings: Settings) -> str:
    """The key this box reaches its embedder with, or a refusal naming the variable to set."""
    field = key_field_of(settings)
    if field is None:
        return ""
    key: str | None = getattr(settings, field)
    if not key:
        raise NoProvider(
            NO_KEY.format(provider=settings.embed_provider, variable=variable_of(field))
        )
    return key
