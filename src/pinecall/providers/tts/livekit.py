"""LiveKit Inference as the voice: `cartesia/sonic-3`, `rime/coda`, `inworld/inworld-tts-2`."""

from livekit.agents import inference
from livekit.agents.types import NOT_GIVEN, NotGivenOr

from pinecall.providers._inference import DEFAULT_TTS, VENDOR, the_projects_pair
from pinecall.providers.registry import Asked, Speech
from pinecall.providers.tts import VENDORS


@VENDORS.registers(VENDOR)
def build(asked: Asked) -> Speech:
    """The voice is the vendor's own id; a model with no voice speaks in that model's default."""
    key, secret = the_projects_pair(asked)
    return inference.TTS(
        model=asked.model or DEFAULT_TTS,
        voice=_or_the_models_own(asked.voice_id),
        language=_or_the_models_own(asked.language),
        api_key=key,
        api_secret=secret,
    )


def _or_the_models_own(value: str | None) -> NotGivenOr[str]:
    """An undeclared voice or language is the model's own, which beats a hint nobody chose."""
    return value or NOT_GIVEN
