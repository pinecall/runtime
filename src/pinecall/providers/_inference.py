"""LiveKit Inference: the vendors LiveKit fronts itself, on this box's own LiveKit project."""

# Every other row of providers/catalog.py is a vendor account: a plugin to install and a key
# of that vendor's to hold. This one is neither. LiveKit Inference is a gateway in front of
# OpenAI, Google, Deepgram, Cartesia, AssemblyAI, Inworld, xAI, Rime, Speechmatics and the rest; a
# model is named `<vendor>/<model>` and the bill lands on the LiveKit project the box already has a
# key and a secret for. So it is the one vendor that works the moment a box exists, and it is how a
# tenant tries Cartesia before deciding whether to hold a Cartesia account.
#
# The pair is passed explicitly and never left to the environment: a box holds its secrets as
# systemd credentials, so LIVEKIT_API_KEY is a file the process reads through Settings and is NOT in
# `os.environ` — which is exactly where the library would have looked for it.

from __future__ import annotations

from pinecall._settings import Settings
from pinecall.providers.registry import Asked, NoProvider

# Inference wants a model and has no default of its own, so each modality names one here. They are
# the cheapest sensible thing in each lane, and an agent that cares names its own:
#   llm — Anthropic is NOT fronted by Inference (its LLM list is OpenAI, Google, Kimi, DeepSeek,
#         Z.ai and xAI), so this build's usual default cannot be it.
#   stt — `auto` is Inference's own word for "pick a provider by the language you hear".
#   tts — a bare vendor name means that vendor's current default model.
# The name this vendor is catalogued, declared, registered and refused under, written once.
VENDOR = "livekit"

DEFAULT_LLM = "openai/gpt-5-mini"
DEFAULT_STT_MODEL = "auto"
DEFAULT_TTS_MODEL = "cartesia"

# Said before the call, like every other missing key, and naming both halves: Inference signs with
# the API key AND the secret, and a box with one and not the other fails at the first token.
NO_LIVEKIT = "livekit inference needs this box's LIVEKIT_API_KEY and its secret: {missing}"


def the_projects_pair(asked: Asked) -> tuple[str, str]:
    """The box's LiveKit key and secret, or the refusal that names whichever one is missing."""
    key = asked.settings.livekit_api_key
    secret = asked.settings.livekit_api_secret
    if not key or not secret:
        missing = "neither" if not key and not secret else ("no key" if not key else "no secret")
        raise NoProvider(NO_LIVEKIT.format(missing=missing))
    return key, secret


# The same question a screen asks before the call, so it shows a box with no LiveKit project as
# one that cannot run Inference yet rather than as one that needs a vendor key it will never find.
def the_project_is_there(settings: Settings) -> bool:
    """Whether this box has the pair Inference signs with. api/ops/providers.py draws it."""
    return bool(settings.livekit_api_key and settings.livekit_api_secret)
