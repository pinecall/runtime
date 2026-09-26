"""The vendors' own keys, each under the variable name that vendor's SDK already reads."""

from pydantic import Field
from pydantic_settings import BaseSettings


# The one part of the configuration that is NOT ours to name. Everything Pinecall invented is
# `PINECALL_`-prefixed in settings/schema.py; these carry the vendor's own variable, because a box
# that already exports ANTHROPIC_API_KEY for something else should not export it twice. They
# are a class of their own for that reason and for one more: they are the half of the settings
# that grows every time a vendor is added, and schema.py is the half that must stay readable.
# Settings inherits this, so `Settings.model_fields` still names every field there is.
#
# There is one field per catalogued vendor that has a single-string credential, and the field name
# IS the variable lowercased: providers/catalog.py:settings_field_of depends on that rule and
# tests/providers/test_catalog.py fails the day a field breaks it. So a vendor's key is read with
# no table of vendor-to-field anywhere — adding a row to the catalog and a field here is the whole
# of it. AWS, Google's speech pair and RTZR have no field: their credentials are a chain or a pair,
# never one string, and their plugins read their own environment (providers/plugin.py).
#
# A box holds only the handful it actually runs on. Every one of these is None until somebody sets
# it, an unset one costs nothing, and `pinecall-runtime doctor` prints the names that are set and
# never a value.
class VendorKeys(BaseSettings):
    """Every vendor credential a box may hold, read straight off the vendor's own name."""

    # ── the five this runtime has a tuned file for (providers/llm, /stt, /tts) ──

    anthropic_api_key: str | None = Field(
        default=None,
        validation_alias="ANTHROPIC_API_KEY",
        description="Anthropic, llm. Every provider key keeps the vendor's own variable name.",
    )
    openai_api_key: str | None = Field(
        default=None,
        validation_alias="OPENAI_API_KEY",
        description="OpenAI, llm, stt and tts. The other LLM a call may run on.",
    )
    soniox_api_key: str | None = Field(
        default=None,
        validation_alias="SONIOX_API_KEY",
        description="Soniox, stt and tts. A call needs one key of each role: llm, stt, tts.",
    )
    deepgram_api_key: str | None = Field(
        default=None,
        validation_alias="DEEPGRAM_API_KEY",
        description="Deepgram, stt and tts. Flux and Nova for the ears, Aura for the voice.",
    )
    eleven_api_key: str | None = Field(
        default=None,
        validation_alias="ELEVEN_API_KEY",
        description="ElevenLabs, stt and tts. The voice this runtime speaks with by default.",
    )

    # ── every other vendor livekit-agents ships a plugin for, alphabetically ────
    # None of these needs a file here: providers/plugin.py builds them from the plugin's own
    # signature, and providers/catalog.py is the row that says the vendor exists at all.

    assemblyai_api_key: str | None = Field(
        default=None,
        validation_alias="ASSEMBLYAI_API_KEY",
        description="AssemblyAI, stt. Universal-Streaming.",
    )
    asyncai_api_key: str | None = Field(
        default=None,
        validation_alias="ASYNCAI_API_KEY",
        description="Async AI, tts.",
    )
    azure_speech_key: str | None = Field(
        default=None,
        validation_alias="AZURE_SPEECH_KEY",
        description="Azure Speech, stt and tts. Needs AZURE_SPEECH_REGION beside the key.",
    )
    baseten_api_key: str | None = Field(
        default=None,
        validation_alias="BASETEN_API_KEY",
        description="Baseten, llm, stt and tts. Open models on Baseten's own endpoints.",
    )
    bland_api_key: str | None = Field(
        default=None,
        validation_alias="BLAND_API_KEY",
        description="Bland, tts.",
    )
    camb_api_key: str | None = Field(
        default=None,
        validation_alias="CAMB_API_KEY",
        description="Camb.ai, tts.",
    )
    cartesia_api_key: str | None = Field(
        default=None,
        validation_alias="CARTESIA_API_KEY",
        description="Cartesia, stt and tts. Sonic, and Ink-Whisper for the ears.",
    )
    cerebras_api_key: str | None = Field(
        default=None,
        validation_alias="CEREBRAS_API_KEY",
        description="Cerebras, llm.",
    )
    clova_stt_secret_key: str | None = Field(
        default=None,
        validation_alias="CLOVA_STT_SECRET_KEY",
        description="Naver CLOVA, stt. Needs CLOVA_STT_INVOKE_URL beside the secret.",
    )
    fal_key: str | None = Field(
        default=None,
        validation_alias="FAL_KEY",
        description="fal, stt. Wizper. Batch, not a live socket.",
    )
    fireworks_api_key: str | None = Field(
        default=None,
        validation_alias="FIREWORKS_API_KEY",
        description="Fireworks AI, stt.",
    )
    fish_api_key: str | None = Field(
        default=None,
        validation_alias="FISH_API_KEY",
        description="Fish Audio, tts.",
    )
    gladia_api_key: str | None = Field(
        default=None,
        validation_alias="GLADIA_API_KEY",
        description="Gladia, stt.",
    )
    gnani_api_key: str | None = Field(
        default=None,
        validation_alias="GNANI_API_KEY",
        description="Gnani, stt and tts.",
    )
    google_api_key: str | None = Field(
        default=None,
        validation_alias="GOOGLE_API_KEY",
        description=(
            "Google, llm, stt and tts. Gemini on the key; Speech-to-Text and Text-to-Speech want a "
            "service account."
        ),
    )
    gradium_api_key: str | None = Field(
        default=None,
        validation_alias="GRADIUM_API_KEY",
        description="Gradium, stt and tts.",
    )
    groq_api_key: str | None = Field(
        default=None,
        validation_alias="GROQ_API_KEY",
        description="Groq, llm, stt and tts. Whisper and open models, fast.",
    )
    hume_api_key: str | None = Field(
        default=None,
        validation_alias="HUME_API_KEY",
        description="Hume, tts.",
    )
    inworld_api_key: str | None = Field(
        default=None,
        validation_alias="INWORLD_API_KEY",
        description="Inworld, stt and tts.",
    )
    lmnt_api_key: str | None = Field(
        default=None,
        validation_alias="LMNT_API_KEY",
        description="LMNT, tts.",
    )
    minimax_api_key: str | None = Field(
        default=None,
        validation_alias="MINIMAX_API_KEY",
        description="MiniMax, tts.",
    )
    mistral_api_key: str | None = Field(
        default=None,
        validation_alias="MISTRAL_API_KEY",
        description="Mistral AI, llm, stt and tts.",
    )
    murf_api_key: str | None = Field(
        default=None,
        validation_alias="MURF_API_KEY",
        description="Murf, tts.",
    )
    neuphonic_api_key: str | None = Field(
        default=None,
        validation_alias="NEUPHONIC_API_KEY",
        description="Neuphonic, tts.",
    )
    nvidia_api_key: str | None = Field(
        default=None,
        validation_alias="NVIDIA_API_KEY",
        description="NVIDIA Riva, stt and tts.",
    )
    palabra_api_key: str | None = Field(
        default=None,
        validation_alias="PALABRA_API_KEY",
        description="Palabra, stt and tts.",
    )
    # Both an LLM (Sonar) and the embedder providers/embed/perplexity.py runs on: one account, one
    # variable, and the two readers never learn about each other.
    perplexity_api_key: str | None = Field(
        default=None,
        validation_alias="PERPLEXITY_API_KEY",
        description="Perplexity, llm. Sonar, and the embedder: the contextual model and the flat.",
    )
    resemble_api_key: str | None = Field(
        default=None,
        validation_alias="RESEMBLE_API_KEY",
        description="Resemble AI, tts.",
    )
    respeecher_api_key: str | None = Field(
        default=None,
        validation_alias="RESPEECHER_API_KEY",
        description="Respeecher, tts.",
    )
    rime_api_key: str | None = Field(
        default=None,
        validation_alias="RIME_API_KEY",
        description="Rime, tts.",
    )
    sarvam_api_key: str | None = Field(
        default=None,
        validation_alias="SARVAM_API_KEY",
        description=(
            "Sarvam AI, llm, stt and tts. The Indian languages, all three jobs on one account."
        ),
    )
    simplismart_api_key: str | None = Field(
        default=None,
        validation_alias="SIMPLISMART_API_KEY",
        description="Simplismart, stt and tts.",
    )
    slng_api_key: str | None = Field(
        default=None,
        validation_alias="SLNG_API_KEY",
        description="slng, stt and tts.",
    )
    smallest_api_key: str | None = Field(
        default=None,
        validation_alias="SMALLEST_API_KEY",
        description="Smallest AI, stt and tts.",
    )
    speechify_api_key: str | None = Field(
        default=None,
        validation_alias="SPEECHIFY_API_KEY",
        description="Speechify, tts.",
    )
    speechmatics_api_key: str | None = Field(
        default=None,
        validation_alias="SPEECHMATICS_API_KEY",
        description="Speechmatics, stt and tts.",
    )
    spitch_api_key: str | None = Field(
        default=None,
        validation_alias="SPITCH_API_KEY",
        description="Spitch, stt and tts. The African languages.",
    )
    upliftai_api_key: str | None = Field(
        default=None,
        validation_alias="UPLIFTAI_API_KEY",
        description="Upliftai, tts.",
    )
    vakyam_api_key: str | None = Field(
        default=None,
        validation_alias="VAKYAM_API_KEY",
        description="Vakyam, tts.",
    )
    xai_api_key: str | None = Field(
        default=None,
        validation_alias="XAI_API_KEY",
        description=(
            "xAI, stt and tts. Grok speaks and hears here; Grok the LLM is reached through livekit "
            "or openai."
        ),
    )

    # ── not a call's vendors ───────────────────────────────────────────────────
    # The embedder's other door, and the token a WhatsApp message goes back out with. They sit
    # beside the model vendors because an org may bring its own and the vault reads them the same
    # way; Perplexity is above, under its own name, because it is both an LLM and an embedder.
    openrouter_api_key: str | None = Field(
        default=None,
        validation_alias="OPENROUTER_API_KEY",
        description="OpenRouter, an embedder: the other way to the flat model, no contextual door.",
    )
    whatsapp_access_token: str | None = Field(
        default=None,
        validation_alias="WHATSAPP_ACCESS_TOKEN",
        description="The box's own WhatsApp Cloud API token, used for an org that brought none.",
    )
