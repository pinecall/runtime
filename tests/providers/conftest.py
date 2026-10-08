"""A box's providers configuration, as its operator writes it."""

import pytest

from pinecall.providers.catalog import Providers


@pytest.fixture
def configured() -> Providers:
    """A box's configuration, as an operator would write it from the console."""
    return Providers.model_validate(
        {
            "defaults": {
                "llm": {"vendor": "anthropic", "model": "claude-haiku-5-5"},
                "stt": {"vendor": "deepgram", "model": "flux-general-multi"},
                "tts": {"vendor": "cartesia", "model": "sonic-3"},
            },
            "models": {"tts/elevenlabs": "eleven_flash_v2_5"},
            "voices": {"cartesia/es": "voice-marta", "cartesia/en": "voice-katie"},
            "tuning": {
                "stt/deepgram": {
                    "builds": "STTv2",
                    "options": {"eot_timeout_ms": 1000},
                    "ends_the_turn": True,
                },
                "llm/anthropic": {"options": {"caching": "ephemeral"}},
            },
            "hints": ["es", "en"],
            "rates": {
                "claude-haiku-5-5": {"input": 1.0, "output": 5.0, "cached_input": 0.1},
                "sonic": {"characters": 0.00003},
                "flux": {"audio_seconds": 0.0001},
            },
        }
    )
