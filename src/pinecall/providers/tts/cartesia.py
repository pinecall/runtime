"""Cartesia: the voice — Sonic, whose plugin speaks English unless it is told otherwise."""

from livekit.plugins import cartesia
from livekit.plugins.cartesia.models import TTSDefaultVoiceId

from pinecall.providers.registry import Asked, Speech, a_key
from pinecall.providers.tts import VENDORS

# The models this build vouches for, the default first: sonic-3 is Cartesia's newest and its
# fastest to the first audio of the two we measured (2026-09-25: 0.2-0.7 s against 0.2-1.6 s for
# sonic-2, six Spanish voices, one sentence each). sonic-2 stays for an agent tuned on it.
DEFAULT_MODEL = "sonic-3"
MODELS = (DEFAULT_MODEL, "sonic-2")


# The one trap: the plugin's own `language` defaults to "en", so an agent that declared none would
# have a Spanish voice read Spanish words as English. A declared language is always sent; an
# undeclared one leaves the plugin's default, which is at least the vendor's documented behaviour.
@VENDORS.registers("cartesia", models=MODELS)
def build(asked: Asked) -> Speech:
    """The model, the voice and the language, each the agent's own or Cartesia's default."""
    speaks = cartesia.TTS(
        model=asked.model or DEFAULT_MODEL,
        api_key=a_key("cartesia", asked),
        voice=asked.voice_id or TTSDefaultVoiceId,
    )
    if asked.language:
        speaks.update_options(language=asked.language)
    return speaks
