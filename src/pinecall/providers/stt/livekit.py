"""LiveKit Inference as the ears: `auto` picks a provider by the language, or name one."""

from livekit.agents import inference

from pinecall.providers.livekit_inference import DEFAULT_STT_MODEL, VENDOR, livekit_pair
from pinecall.providers.registry import Asked, Ears
from pinecall.providers.stt import VENDORS


# Nothing about `asked.hears` or endpointing here: Inference holds both server-side per model, and
# the session hands it whatever keyterms the model advertises support for, exactly as it does for
# a plugin (stt/stt.py:286). What is passed is the one thing the gateway cannot infer: the
# language, which is also what turns `auto` from a guess into a choice.
@VENDORS.registers(VENDOR, models=(DEFAULT_STT_MODEL,))
def build(asked: Asked) -> Ears:
    """`deepgram/nova-3`, `assemblyai/universal-streaming`, or `auto` to be told by the language."""
    key, secret = livekit_pair(asked)
    return inference.STT(
        model=asked.model or DEFAULT_STT_MODEL,
        language=asked.language or "multi",
        api_key=key,
        api_secret=secret,
    )
