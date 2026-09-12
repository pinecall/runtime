"""The vendors' own keys, each under the variable name that vendor's SDK already reads."""

from pydantic import Field
from pydantic_settings import BaseSettings


# The one part of the configuration that is NOT ours to name. Everything Pinecall invented is
# `PINECALL_`-prefixed in _settings.py; these carry the vendor's own variable, because a box
# that already exports ANTHROPIC_API_KEY for something else should not export it twice. They
# are a class of their own for that reason and for one more: they are the half of the settings
# that grows every time a vendor is added, and _settings.py is the half that must stay readable.
# Settings inherits this, so `Settings.model_fields` still names every field there is.
class VendorKeys(BaseSettings):
    """Every vendor credential a box may hold, read straight off the vendor's own name."""

    anthropic_api_key: str | None = Field(
        default=None,
        validation_alias="ANTHROPIC_API_KEY",
        description="Anthropic, an LLM. Every provider key keeps the vendor's own variable name.",
    )
    openai_api_key: str | None = Field(
        default=None,
        validation_alias="OPENAI_API_KEY",
        description="OpenAI, the other LLM a call may run on.",
    )
    soniox_api_key: str | None = Field(
        default=None,
        validation_alias="SONIOX_API_KEY",
        description="Soniox, an STT. A call needs one key of each role: llm, stt, tts.",
    )
    deepgram_api_key: str | None = Field(
        default=None,
        validation_alias="DEEPGRAM_API_KEY",
        description="Deepgram, the other STT.",
    )
    eleven_api_key: str | None = Field(
        default=None,
        validation_alias="ELEVEN_API_KEY",
        description="ElevenLabs, the TTS.",
    )
    # Not a call's vendors: the two the EMBEDDER may run on, read only by providers/embed.
    perplexity_api_key: str | None = Field(
        default=None,
        validation_alias="PERPLEXITY_API_KEY",
        description="Perplexity, an embedder: the contextual model and the flat one, direct.",
    )
    openrouter_api_key: str | None = Field(
        default=None,
        validation_alias="OPENROUTER_API_KEY",
        description="OpenRouter, the other way to the flat model. It serves no contextual door.",
    )
    # Not a model vendor: the token the Graph API takes when a message goes back out. It sits
    # beside the others because an org may bring its own, and the registry reads both the same way.
    whatsapp_access_token: str | None = Field(
        default=None,
        validation_alias="WHATSAPP_ACCESS_TOKEN",
        description="The box's own WhatsApp Cloud API token, used for an org that brought none.",
    )
