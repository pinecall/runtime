"""Deepgram Flux: the alternate ears, on the v2 socket, where the multilingual model lives."""

from livekit.agents.types import NOT_GIVEN, NotGivenOr
from livekit.plugins import deepgram

from pinecall.providers.registry import Asked, Ears, a_key
from pinecall.providers.stt import MAX_SILENCE_MS, VENDORS, hints_for

# flux-general-multi is the only Flux model that takes language hints at all; the plugin's own
# default is flux-general-en (deepgram/stt_v2.py:73), which would quietly ignore a Spanish agent.
DEFAULT_MODEL = "flux-general-multi"
# The two the v2 socket speaks (deepgram/models.py:41): nova is v1's and would go out silent here.
MODELS = (DEFAULT_MODEL, "flux-general-en")


# Nothing about `asked.hears` here on purpose: Flux advertises the keyterms capability
# (deepgram/stt_v2.py:124), so the session hands it the agent's declared words itself and merges
# them with whatever the socket was opened with (stt/stt.py:286, deepgram/stt_v2.py:304-308).
# Soniox advertises none, which is why soniox.py has a door of its own to fill.
@VENDORS.registers("deepgram", models=MODELS)
def build(asked: Asked) -> Ears:
    """STTv2 is the Flux door: STT (v1) speaks nova and knows nothing about end-of-turn."""
    return deepgram.STTv2(
        model=asked.model or DEFAULT_MODEL,
        api_key=a_key("deepgram", asked),
        # The plugin sends neither (deepgram/stt_v2.py:78,79); its own 16 kHz we keep as it is.
        language_hint=hints_for(asked.language),
        eot_timeout_ms=asked.endpointing_ms or MAX_SILENCE_MS,
        # Flux ends a turn on a confidence, and the timeout above only catches the silences it is
        # unsure about: a caller it is CONFIDENTLY wrong about is cut whatever the timeout says.
        # Deepgram's guidance for that is this bar, not the clock (docs/flux/configuration).
        eot_threshold=_or_the_plugins(asked.eot_threshold),
        eager_eot_threshold=_or_the_plugins(asked.eager_eot_threshold),
    )


# The plugin's own defaults are NOT_GIVEN, a sentinel of livekit's, and passing None instead would
# send `null` on a socket that refuses it. An undeclared knob is simply not mentioned.
def _or_the_plugins(asked: float | None) -> NotGivenOr[float]:
    """One confidence as the plugin takes it: the number, or its own default left alone."""
    return NOT_GIVEN if asked is None else asked
