"""LiveKit Inference as the model: `livekit` + `openai/gpt-5-mini`, on the box's own project."""

from livekit.agents import inference

from pinecall.providers.livekit_inference import DEFAULT_LLM, VENDOR, livekit_pair
from pinecall.providers.llm import VENDORS
from pinecall.providers.registry import Asked, Chat


@VENDORS.registers(VENDOR, models=(DEFAULT_LLM,))
def build(asked: Asked) -> Chat:
    """The model is `<vendor>/<model>`: the vendor is inside the name, not beside it."""
    key, secret = livekit_pair(asked)
    return inference.LLM(model=asked.model or DEFAULT_LLM, api_key=key, api_secret=secret)
