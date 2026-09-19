"""Anthropic: the plugin IS the adapter, and the static prompt blocks are what a cache is for."""

from livekit.plugins import anthropic

from pinecall.providers.llm import VENDORS
from pinecall.providers.registry import Asked, Chat, a_key

# The plugin's own is claude-sonnet-4-6 (anthropic/llm.py:67), which is not what a phone line
# waits for.
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
# The family as this build prices it (providers/prices.py), the phone line's default first.
MODELS = (DEFAULT_MODEL, "claude-sonnet-5", "claude-opus-5")


@VENDORS.registers("anthropic", models=MODELS)
def build(asked: Asked) -> Chat:
    """caching is unset in the plugin (anthropic/llm.py:77); the static blocks are its reason."""
    return anthropic.LLM(
        model=asked.model or DEFAULT_MODEL,
        api_key=a_key("anthropic", asked),
        caching="ephemeral",
    )
