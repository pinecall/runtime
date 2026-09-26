"""The way from an agent's declaration to a model that can answer it, over the llm vendor table."""

from __future__ import annotations

from collections.abc import Callable

from pinecall._settings import Settings
from pinecall.providers import llm
from pinecall.providers.registry import Asked, Chat, NoProvider
from pinecall.types import Brought, Model

__all__ = ["DEFAULT_VENDOR", "Chat", "Models", "NoProvider", "models_for", "vendor_of"]

# The vendor a session runs when the app declared none. Which of that vendor's models it runs is
# the vendor file's own business — providers/llm/anthropic.py — so no model name is written twice.
DEFAULT_VENDOR = "anthropic"

type Models = Callable[[Model | None, Brought], Chat]
"""What the gateway holds: what an agent asked for, whose keys and what is lent, and the model."""


# The box's keys are the PROCESS's and are closed over once here; the org's are the SESSION's and
# are an argument, which is the split worker/kit.py makes for the same reason: one process serves
# many tenants at once, and a tenant's key held in a closure would be one tenant's key in another
# tenant's call. Nothing below knows which of the two it got — see docs/decisions/provider-keys.md.
def models_for(settings: Settings) -> Models:
    """The process's way to a model: a declaration and an org's keys in, a plugin out."""

    def a_model(asked: Model | None, brought: Brought) -> Chat:
        vendor = asked.provider if asked else DEFAULT_VENDOR
        wanted = Asked(
            settings=settings,
            model=asked.model if asked else None,
            keys=brought.keys,
            lends=brought.lends,
        )
        return llm.VENDORS.build(vendor, wanted)

    return a_model


# livekit's own `LLM.provider` is the API host the client points at — "api.anthropic.com" — which
# is the right answer for a trace and the wrong one for a price table. The vendor is the file the
# plugin came from, and the plugin names itself in `LLM.label`: livekit builds it as
# "<module>.<class>", so an anthropic model answers "livekit.plugins.anthropic.llm.LLM". That
# public label is what is read here — never the private __module__, and never the URL.
#
# Read by POSITION and not by "which catalogued name appears somewhere in the label". That
# shortcut worked while no vendor was called `livekit`, and the day one was — LiveKit Inference,
# which is a vendor of ours now — EVERY label answered `livekit`, because every label begins with
# it. A plugin puts its vendor in the third segment; Inference has no vendor of its own there
# because it is the gateway itself, and the model name is where its vendor is written.
PLUGINS = ("livekit", "plugins")
INFERENCE = ("livekit", "agents", "inference")


def vendor_of(model: Chat) -> str:
    """The name prices and usage rows know a model by: its plugin's vendor, not its host."""
    named = tuple(model.label.split("."))
    if named[:2] == PLUGINS and len(named) > 2:
        return named[2]
    if named[:3] == INFERENCE:
        return "livekit"
    return model.provider
