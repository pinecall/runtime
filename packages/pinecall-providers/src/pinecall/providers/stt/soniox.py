"""Soniox: the default ears — one realtime socket, endpointing we name, language hints we choose."""

from dataclasses import replace

from livekit.plugins import soniox

from pinecall.providers.registry import Asked, Ears, vendor_key
from pinecall.providers.stt import MAX_SILENCE_MS, VENDORS, hints_for

# How readily the model calls a caller done. Level 2 of 3 trades a little accuracy for latency and
# sensitivity 0.3 leans towards finalising; the plugin leaves both unset (soniox/stt.py:129,135),
# which is the server's own guess. Both arrived with v5 and earlier models reject them.
LATENCY_LEVEL = 2
SENSITIVITY = 0.3


# Soniox advertises no keyterms capability (soniox/stt.py:185-191), so livekit's own session-level
# keyterms never reach it; its door is `context.terms`, which the v3-and-up models read as words
# worth expecting (soniox/stt.py:74-84,116).
def _the_words_it_expects(hears: tuple[str, ...]) -> soniox.ContextObject | None:
    """The agent's declared words in Soniox's own shape, or nothing when it declared none."""
    return soniox.ContextObject(terms=list(hears)) if hears else None


# The plugin's own default, and the one before it, as the plugin spells them (soniox/stt.py).
MODELS = ("stt-rt-v5", "stt-rt-v3-preview")


@VENDORS.registers("soniox", models=MODELS)
def build(asked: Asked) -> Ears:
    """Only what differs from the plugin: its model and its 16 kHz are already what we want."""
    params = soniox.STTOptions(
        language_hints=hints_for(asked.language),  # the plugin sends none (soniox/stt.py:114)
        endpoint_latency_adjustment_level=LATENCY_LEVEL,
        endpoint_sensitivity=SENSITIVITY,
        max_endpoint_delay_ms=asked.endpointing_ms or MAX_SILENCE_MS,
        context=_the_words_it_expects(asked.hears),
    )
    # The agent's model when it named one; otherwise the plugin's own stt-rt-v5 (soniox/stt.py:112).
    if asked.model:
        params = replace(params, model=asked.model)
    return soniox.STT(api_key=vendor_key("soniox", asked), params=params)
