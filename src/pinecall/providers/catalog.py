"""Every provider LiveKit reaches, in one table: who they are, what they do, what pays them."""

# This file exists because "which vendors does Pinecall run?" used to be answered by counting the
# files under `llm/`, `stt/` and `tts/` — five of them — while livekit-agents 1.8 ships plugins for
# ten times that. A tenant who wanted Cartesia was told `no tts vendor named 'cartesia'` by a build
# that was one import away from having it.
#
# The rows are read off livekit-agents 1.8.0 itself: `does` is what the plugin's own `__all__`
# exports, and `env` is the variable that plugin reads its key from when nobody passes one. Nothing
# here is invented — a row that disagrees with its plugin is a bug in the row, and
# tests/providers/test_catalog.py checks the two against each other for whatever is installed.
#
# What is NOT here: how each plugin spells its constructor arguments. The plugins disagree —
# `voice`, `voice_id`, `voice_uuid`, `speaker`, `voice_name` — and a table of that would rot at the
# next release. providers/plugin.py reads the signature instead, at the moment it builds one.

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# The three jobs a call is built out of. Who notices speech and who calls the turn are livekit's
# own and are not a vendor choice here — see providers/pipeline.py.
type Modality = Literal["llm", "stt", "tts"]

MODALITIES: tuple[Modality, ...] = ("llm", "stt", "tts")


@dataclass(frozen=True)
class Provider:
    """One vendor account: what it can do, which plugin reaches it, and where its key lives."""

    name: str
    """The one spelling a declaration, an override and a stored key use. Aliases resolve here."""

    does: tuple[Modality, ...] = ()
    """Which of the three this vendor's plugin exports a class for. Empty: it is not a model
    vendor at all, and only its key is of interest (whatsapp)."""

    env: str | None = None
    """The variable this vendor's own SDK reads, kept as the vendor spells it (see _vendor_keys.py).
    None: the plugin brings its own credentials — an AWS profile, a Google service account, a
    client id and secret — so there is no one string an org could bring and BYOK does not apply."""

    aliases: tuple[str, ...] = ()
    """Every other word a person writes for this vendor. `11labs` is the one that started this.

    A word here must not also be a MODEL of that vendor: a bare word that names a vendor IS the
    vendor at the pipeline door (providers/overrides.py), so `sonic`, `octave`, `sonar`, `mist`,
    `nova` and `aura` are deliberately NOT aliases — every one of them is something a person could
    reasonably type meaning the model."""

    note: str = ""
    """One line for a screen: what a person is choosing when they choose this."""

    module: str | None = None
    """The module under `livekit.plugins`, when it is not the name. "" means there is no plugin:
    `livekit` is livekit-agents itself and `whatsapp` is not a plugin at all."""

    @property
    def plugin(self) -> str:
        """The module under `livekit.plugins`. Empty when this vendor needs no plugin."""
        return self.name if self.module is None else self.module

    @property
    def extra(self) -> str:
        """What installs it: `pip install "livekit-agents[<extra>]"`. Empty when nothing does."""
        return self.plugin


# ── the table ───────────────────────────────────────────────────────────────────
#
# Positional, in the order the fields are declared above — name, does, env, aliases, note — so a
# row reads as one sentence and the file stays a table rather than a form. Alphabetical, because
# there is no other order that survives a new row.
#
# `livekit` is first and out of order on purpose: it is the only row that is not a vendor account
# at all. LiveKit Inference fronts OpenAI, Google, Deepgram, Cartesia, AssemblyAI, Inworld, xAI and
# the rest from LiveKit's own gateway, billed to the LiveKit project this box already has a key and
# a secret for — so it needs no vendor key, no extra and no install, and its model names carry the
# vendor: `livekit` + `openai/gpt-5-mini`. It is the answer to "can I try Cartesia right now".
PROVIDERS: tuple[Provider, ...] = (
    Provider(
        "livekit",
        ("llm", "stt", "tts"),
        None,
        ("inference", "lk"),
        "LiveKit Inference: every vendor it fronts, on the box's own project, with no vendor key",
        module="",
    ),
    Provider(
        "anthropic",
        ("llm",),
        "ANTHROPIC_API_KEY",
        ("claude",),
        "Claude. What this runtime thinks with when an agent names nothing",
    ),
    Provider("assemblyai", ("stt",), "ASSEMBLYAI_API_KEY", ("assembly",), "Universal-Streaming"),
    Provider("asyncai", ("tts",), "ASYNCAI_API_KEY", ("async",)),
    Provider(
        "aws",
        ("llm", "stt", "tts"),
        None,
        ("bedrock", "polly", "transcribe", "amazon"),
        "Bedrock, Transcribe and Polly. Credentials are the AWS chain, never a brought key",
    ),
    Provider(
        "azure",
        ("stt", "tts"),
        "AZURE_SPEECH_KEY",
        ("microsoft",),
        "Azure Speech. Wants AZURE_SPEECH_REGION beside the key",
    ),
    Provider(
        "baseten",
        ("llm", "stt", "tts"),
        "BASETEN_API_KEY",
        (),
        "Open models on Baseten's own endpoints",
    ),
    Provider("bland", ("tts",), "BLAND_API_KEY"),
    Provider("cambai", ("tts",), "CAMB_API_KEY", ("camb",)),
    Provider(
        "cartesia",
        ("stt", "tts"),
        "CARTESIA_API_KEY",
        (),
        "Sonic for the voice, Ink-Whisper for the ears",
    ),
    Provider("cerebras", ("llm",), "CEREBRAS_API_KEY"),
    Provider(
        "clova",
        ("stt",),
        "CLOVA_STT_SECRET_KEY",
        ("naver",),
        "Wants CLOVA_STT_INVOKE_URL beside the secret",
    ),
    Provider(
        "deepgram",
        ("stt", "tts"),
        "DEEPGRAM_API_KEY",
        ("dg",),
        "Flux and Nova for the ears (stt/deepgram.py), Aura for the voice",
    ),
    Provider(
        "elevenlabs",
        ("stt", "tts"),
        "ELEVEN_API_KEY",
        ("11labs", "eleven", "elevenlab"),
        "What this runtime speaks with when an agent names nothing (tts/elevenlabs.py)",
    ),
    Provider("fal", ("stt",), "FAL_KEY", (), "Wizper. Batch, not a live socket"),
    Provider("fireworksai", ("stt",), "FIREWORKS_API_KEY", ("fireworks",)),
    Provider("fishaudio", ("tts",), "FISH_API_KEY", ("fish",)),
    Provider("gladia", ("stt",), "GLADIA_API_KEY"),
    Provider("gnani", ("stt", "tts"), "GNANI_API_KEY"),
    Provider(
        "google",
        ("llm", "stt", "tts"),
        "GOOGLE_API_KEY",
        ("gemini", "vertex", "vertexai"),
        "Gemini runs on the key; Speech-to-Text and Text-to-Speech want a service account",
    ),
    Provider("gradium", ("stt", "tts"), "GRADIUM_API_KEY"),
    Provider(
        "groq", ("llm", "stt", "tts"), "GROQ_API_KEY", (), "Whisper and the open models, fast"
    ),
    Provider("hume", ("tts",), "HUME_API_KEY"),
    Provider("inworld", ("stt", "tts"), "INWORLD_API_KEY"),
    Provider("lmnt", ("tts",), "LMNT_API_KEY"),
    Provider("minimax", ("tts",), "MINIMAX_API_KEY"),
    Provider("mistralai", ("llm", "stt", "tts"), "MISTRAL_API_KEY", ("mistral",)),
    Provider("murf", ("tts",), "MURF_API_KEY"),
    Provider("neuphonic", ("tts",), "NEUPHONIC_API_KEY"),
    Provider("nvidia", ("stt", "tts"), "NVIDIA_API_KEY", ("riva",)),
    Provider(
        "openai",
        ("llm", "stt", "tts"),
        "OPENAI_API_KEY",
        ("gpt", "chatgpt"),
        "GPT, Whisper and the OpenAI voices (llm/openai.py)",
    ),
    Provider("palabra", ("stt", "tts"), "PALABRA_API_KEY"),
    Provider(
        "perplexity",
        ("llm",),
        "PERPLEXITY_API_KEY",
        (),
        "Sonar. The same account providers/embed/perplexity.py embeds on",
    ),
    Provider("resemble", ("tts",), "RESEMBLE_API_KEY"),
    Provider("respeecher", ("tts",), "RESPEECHER_API_KEY"),
    Provider("rime", ("tts",), "RIME_API_KEY"),
    Provider(
        "rtzr",
        ("stt",),
        None,
        ("returnzero", "vito"),
        "Korean. Takes a client id and a secret, so BYOK does not reach it",
    ),
    Provider(
        "sarvam",
        ("llm", "stt", "tts"),
        "SARVAM_API_KEY",
        (),
        "The Indian languages, all three jobs on one account",
    ),
    Provider("simplismart", ("stt", "tts"), "SIMPLISMART_API_KEY"),
    Provider("slng", ("stt", "tts"), "SLNG_API_KEY"),
    Provider("smallestai", ("stt", "tts"), "SMALLEST_API_KEY", ("smallest",)),
    Provider(
        "soniox",
        ("stt", "tts"),
        "SONIOX_API_KEY",
        (),
        "What this runtime hears with when an agent names nothing (stt/soniox.py)",
    ),
    Provider("speechify", ("tts",), "SPEECHIFY_API_KEY"),
    Provider("speechmatics", ("stt", "tts"), "SPEECHMATICS_API_KEY"),
    Provider("spitch", ("stt", "tts"), "SPITCH_API_KEY", (), "The African languages"),
    Provider("upliftai", ("tts",), "UPLIFTAI_API_KEY", ("uplift",)),
    Provider("vakyam", ("tts",), "VAKYAM_API_KEY"),
    Provider(
        "xai",
        ("stt", "tts"),
        "XAI_API_KEY",
        ("grok",),
        "Grok speaks and hears here; Grok the LLM is reached through livekit or openai",
    ),
    # Last, and not a model vendor at all: the Cloud API token a WhatsApp message goes back out
    # with. It is here because an org brings it the same way it brings any other key, through the
    # same vault and the same door, and a second list of "vendors an org may bring a key for" is
    # how the two would drift. It does no modality, so nothing ever builds a session out of it.
    Provider(
        "whatsapp",
        (),
        "WHATSAPP_ACCESS_TOKEN",
        (),
        "The WhatsApp Cloud API token a message goes back out with, not a model vendor",
        module="",
    ),
)


# ── reading the table ───────────────────────────────────────────────────────────

BY_NAME: dict[str, Provider] = {row.name: row for row in PROVIDERS}


# Every word that resolves to a provider: its own name and every alias, in one map. A duplicate
# would be a silent shadow — one vendor quietly answering for another — so the build refuses one.
def _every_word() -> dict[str, str]:
    """Name and alias to canonical name, built once, refusing a word that names two vendors."""
    words: dict[str, str] = {}
    for row in PROVIDERS:
        for word in (row.name, *row.aliases):
            taken = words.get(word)
            if taken is not None and taken != row.name:
                raise AssertionError(f"providers/catalog.py: {word!r} names {taken} and {row.name}")
            words[word] = row.name
    return words


WORDS: dict[str, str] = _every_word()


def named(word: str) -> Provider | None:
    """The provider a person meant, by its own name or any alias. None: nobody by that word."""
    return BY_NAME.get(WORDS.get(word.strip().lower(), ""))


def canonical(word: str) -> str:
    """The one spelling of a vendor, from any of its words. An unknown word is returned as typed:
    the door that refuses it says so in its own sentence, and this one never invents a name."""
    return WORDS.get(word.strip().lower(), word.strip().lower())


def doing(modality: Modality) -> tuple[Provider, ...]:
    """Every provider that can do this job, in the order a list on a screen shows them."""
    return tuple(row for row in PROVIDERS if modality in row.does)


def env_of(vendor: str) -> str | None:
    """The variable the box reads this vendor's key from. None: it brings its own credentials."""
    row = named(vendor)
    return None if row is None else row.env


# The settings field that holds a vendor's key is the vendor's own variable, lowercased — that is
# the whole rule, and _vendor_keys.py is written to keep it true. It replaces a hand-kept table of
# vendor-to-field that had six rows and would have needed one per vendor.
def settings_field_of(vendor: str) -> str | None:
    """Which field of Settings holds this vendor's key, or None when it has no single one."""
    env = env_of(vendor)
    return None if env is None else env.lower()


# Every vendor an org may bring a key for: the ones with a single-string credential. AWS, Google's
# speech pair and RTZR are left out on purpose — there is no one string to store for them, and a
# door that accepted one would store a key nothing reads.
def vendors_with_a_key() -> tuple[str, ...]:
    """The vendors BYOK reaches, sorted: what api/provider_keys.py accepts and lists."""
    return tuple(sorted(row.name for row in PROVIDERS if row.env is not None))
