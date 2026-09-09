"""The way from an agent's declaration to a model that can answer it, over the llm vendor table."""

from __future__ import annotations

from collections.abc import Callable

from pinecall._settings import Settings
from pinecall.providers import llm
from pinecall.providers.registry import Asked, Chat, NoProvider
from pinecall.types import Model, ProviderKeys

__all__ = ["Chat", "DEFAULT_VENDOR", "Models", "NoProvider", "models_for", "vendor_of"]

# The vendor a session runs when the app declared none. Which of that vendor's models it runs is
# the vendor file's own business — providers/llm/anthropic.py — so no model name is written twice.
DEFAULT_VENDOR = "anthropic"

type Models = Callable[[Model | None, ProviderKeys], Chat]
"""What the gateway holds: what an agent asked for and whose keys, and it has the model."""


# The box's keys are the PROCESS's and are closed over once here; the org's are the SESSION's and
# are an argument, which is the split worker/kit.py makes for the same reason: one process serves
# many tenants at once, and a tenant's key held in a closure would be one tenant's key in another
# tenant's call. Nothing below knows which of the two it got — see docs/decisions/provider-keys.md.
def models_for(settings: Settings) -> Models:
    """The process's way to a model: a declaration and an org's keys in, a plugin out."""

    def a_model(asked: Model | None, keys: ProviderKeys) -> Chat:
        vendor = asked.provider if asked else DEFAULT_VENDOR
        wanted = Asked(settings=settings, model=asked.model if asked else None, keys=keys)
        return llm.VENDORS.build(vendor, wanted)

    return a_model


# livekit's own `LLM.provider` is the API host the client points at — "api.anthropic.com" — which
# is the right answer for a trace and the wrong one for a price table. The vendor is the file the
# plugin came from, and the plugin names itself in `LLM.label`: livekit builds it as
# "<module>.<class>", so an anthropic model answers "livekit.plugins.anthropic.llm.LLM". That
# public label is what is read here — never the private __module__, and never the URL.
def vendor_of(model: Chat) -> str:
    """The name prices and usage rows know a model by: its plugin's vendor, not its host."""
    named = model.label.split(".")
    for vendor in llm.VENDORS.names:
        if vendor in named:
            return vendor
    return model.provider
