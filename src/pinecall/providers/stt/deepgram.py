"""Deepgram Flux: the alternate ears, on the v2 socket, where the multilingual model lives."""

from livekit.plugins import deepgram

from pinecall.providers.registry import Asked, Ears, a_key
from pinecall.providers.stt import MAX_SILENCE_MS, VENDORS, hints_for

# flux-general-multi is the only Flux model that takes language hints at all; the plugin's own
# default is flux-general-en (deepgram/stt_v2.py:73), which would quietly ignore a Spanish agent.
DEFAULT_MODEL = "flux-general-multi"


# Nothing about `asked.hears` here on purpose: Flux advertises the keyterms capability
# (deepgram/stt_v2.py:124), so the session hands it the agent's declared words itself and merges
# them with whatever the socket was opened with (stt/stt.py:286, deepgram/stt_v2.py:304-308).
# Soniox advertises none, which is why soniox.py has a door of its own to fill.
@VENDORS.registers("deepgram")
def build(asked: Asked) -> Ears:
    """STTv2 is the Flux door: STT (v1) speaks nova and knows nothing about end-of-turn."""
    return deepgram.STTv2(
        model=asked.model or DEFAULT_MODEL,
        api_key=a_key("deepgram", asked),
        # The plugin sends neither (deepgram/stt_v2.py:78,79); its own 16 kHz we keep as it is.
        language_hint=hints_for(asked.language),
        eot_timeout_ms=asked.endpointing_ms or MAX_SILENCE_MS,
    )
