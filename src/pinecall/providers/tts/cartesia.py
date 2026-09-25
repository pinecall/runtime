"""Cartesia: the voice — Sonic, whose plugin speaks English unless it is told otherwise."""

from livekit.plugins import cartesia

from pinecall.providers.registry import Asked, Speech, a_key
from pinecall.providers.tts import VENDORS

# The models this build vouches for, the default first: sonic-3 is Cartesia's newest and its
# fastest to the first audio of the two we measured (2026-09-25: 0.2-0.7 s against 0.2-1.6 s for
# sonic-2, six Spanish voices, one sentence each). sonic-2 stays for an agent tuned on it.
DEFAULT_MODEL = "sonic-3"
MODELS = (DEFAULT_MODEL, "sonic-2")

# The voice an agent that chose none speaks in, by its language: Cartesia's own support voices,
# native speakers each, so a Spanish agent is not read by an American. Spain's for `es`, because
# the tenants who speak it here are in Spain (Mexico's voices are `es` too: the picker tells them
# apart by country). Any other language is read by the English one — sonic-3 is multilingual.
VOICE_FOR: dict[str, str] = {
    "es": "de38f545-c574-44e8-9b54-a7d6fec1c6b1",  # Marta - Friendly Guide: Spain, castilian
    "en": "f786b574-daa5-4673-aa0c-cbe3e8534c02",  # Katie - Friendly Fixer: the plugin's own
}


# The one trap: the plugin's own `language` defaults to "en", so an agent that declared none would
# have a Spanish voice read Spanish words as English. A declared language is always sent; an
# undeclared one leaves the plugin's default, which is at least the vendor's documented behaviour.
@VENDORS.registers("cartesia", models=MODELS)
def build(asked: Asked) -> Speech:
    """The model, the voice and the language, each the agent's own or Cartesia's default."""
    speaks = cartesia.TTS(
        model=asked.model or DEFAULT_MODEL,
        api_key=a_key("cartesia", asked),
        voice=asked.voice_id or a_voice_for(asked.language),
    )
    if asked.language:
        speaks.update_options(language=asked.language)
    return speaks


def a_voice_for(language: str | None) -> str:
    """The voice an agent that chose none speaks in: its language's, else the English one."""
    return VOICE_FOR.get(language or "", VOICE_FOR["en"])
