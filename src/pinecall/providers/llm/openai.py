"""OpenAI: the model and the key, and nothing of ours between the session and the provider."""

from livekit.plugins import openai

from pinecall.providers.llm import VENDORS
from pinecall.providers.registry import Asked, Chat, a_key

# The plugin's own is gpt-4.1 (openai/llm.py:92), a generation behind what a call should think on.
DEFAULT_MODEL = "gpt-5-mini"
# The ones providers/prices.py has a row for, behind the default.
MODELS = (DEFAULT_MODEL, "gpt-4.1-mini", "gpt-4.1", "gpt-4o-mini")


@VENDORS.registers("openai", models=MODELS)
def build(asked: Asked) -> Chat:
    """One row, one plugin: livekit streams it, measures it and names it for the price table."""
    return openai.LLM(
        model=asked.model or DEFAULT_MODEL,
        api_key=a_key("openai", asked),
    )
