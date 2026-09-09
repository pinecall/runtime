"""ElevenLabs: the voice — and the one plugin whose own default is a model this repo forbids."""

import logging

from livekit.agents.types import NOT_GIVEN, NotGivenOr
from livekit.plugins import elevenlabs

from pinecall.providers.registry import Asked, NoProvider, Speech, a_key
from pinecall.providers.tts import VENDORS

logger = logging.getLogger(__name__)

# Ours, never the plugin's: livekit's TTS() signature defaults to eleven_turbo_v2_5, so a build
# that forwarded a missing model would ship what the milestone forbids (livekit-session.md #16).
DEFAULT_MODEL = "eleven_flash_v2_5"

# The models this repo will not run, each with the one it runs instead. turbo_v2_5 is the plugin's
# inherited default; eleven_v3 is the dialogue model without the conversational tuning. Neither is
# refused outright — an agent that asked for a voice gets a voice, and the log says which.
INSTEAD = {
    "eleven_turbo_v2_5": DEFAULT_MODEL,
    "eleven_v3": "eleven_v3_conversational",
}

# What an agent may ask for by name. Anything else is a typo, and a typo must not reach the vendor.
ALLOWED = frozenset({DEFAULT_MODEL, "eleven_v3_conversational", "eleven_multilingual_v2"})


@VENDORS.registers("elevenlabs")
def build(asked: Asked) -> Speech:
    """The model is the whole of it: the plugin's own default is one this repo forbids."""
    return elevenlabs.TTS(
        model=a_model(asked.model),
        api_key=a_key("elevenlabs", asked),
        # The agent's voice, or the plugin's own (tts.py:94,107). sync_alignment is what a tts.word
        # entry is made of and the plugin already has it on (tts.py:124), so it is not set here.
        voice_id=asked.voice_id or elevenlabs.DEFAULT_VOICE_ID,
        language=_a_language(asked.language),
    )


def a_model(wanted: str | None) -> str:
    """The model this build actually runs: ours by default, and never one the milestone forbids."""
    if wanted is None:
        return DEFAULT_MODEL
    if instead := INSTEAD.get(wanted):
        logger.warning("elevenlabs %s is not run here; speaking with %s instead", wanted, instead)
        return instead
    if wanted not in ALLOWED:
        known = ", ".join(sorted(ALLOWED))
        raise NoProvider(f"no elevenlabs model {wanted!r}; this build has {known}")
    return wanted


def _a_language(language: str | None) -> NotGivenOr[str]:
    """An undeclared language is the model's own guess, which is better than a wrong hint."""
    return language or NOT_GIVEN
