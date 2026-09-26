"""One agent's declaration becomes the three vendor objects an AgentSession takes, and says so."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from pinecall.providers.language import primary
from pinecall.providers.llm import VENDORS as LLM_VENDORS
from pinecall.providers.models import DEFAULT_VENDOR
from pinecall.providers.registry import Asked, Chat, Ears, Speech
from pinecall.providers.stt import VENDORS as STT_VENDORS
from pinecall.providers.tts import DEFAULT_TTS, curated_voices
from pinecall.providers.tts import VENDORS as TTS_VENDORS
from pinecall.settings import Settings
from pinecall.types import AgentConfig, Brought, Model, Voice

logger = logging.getLogger(__name__)

# The vendor the ears run on when the agent named none; the voice's is providers/tts's, beside
# its vendor table. Which model that vendor then runs is the vendor file's own business. Deepgram
# Flux since 2026-09-25: it decides the end of the turn itself (session/voice/session.py), and the
# local detector over Soniox waited its whole 2.5 s on half the turns of a phone call that day.
DEFAULT_STT = "deepgram"


# Three, not five: who notices speech and who calls the turn are livekit's own, built by the
# session itself (voice/agent_session.py:541-542,606-607) — see docs/decisions/providers.md.
@dataclass(frozen=True)
class Pipeline:
    """What a voice session is built out of: the model, the ears, the voice."""

    llm: Chat
    stt: Ears
    tts: Speech


# The first pipeline of a process pays for importing four vendor plugin packages — measured at
# 1.3 s to 4.2 s inside the job, with the caller already in the room, and 5 ms every time after.
# livekit hands an idle process a hook for exactly this, so the tables are read there instead.
def warm_the_vendor_tables() -> None:
    """Import every vendor file now, so no call pays for it: called before a job is assigned."""
    LLM_VENDORS.read()
    STT_VENDORS.read()
    TTS_VENDORS.read()


# `brought` is the ORG'S, fetched for this call beside the config: its own keys — none means the
# box's environment keys, which is what a managed install is — and which of the box's it is lent.
# providers/registry.py chooses the key and refuses what is not lent, before anything is built.
def pipeline_for(config: AgentConfig, settings: Settings, brought: Brought) -> Pipeline:
    """Every vendor an agent runs on, built for this call out of what the agent declared."""
    return Pipeline(
        llm=LLM_VENDORS.build(
            _vendor(config, "llm", config.llm, DEFAULT_VENDOR), _thinking(config, settings, brought)
        ),
        stt=STT_VENDORS.build(
            _vendor(config, "stt", config.stt, DEFAULT_STT), _hearing(config, settings, brought)
        ),
        tts=TTS_VENDORS.build(
            _vendor(config, "tts", config.voice, DEFAULT_TTS), _speaking(config, settings, brought)
        ),
    )


def _thinking(config: AgentConfig, settings: Settings, brought: Brought) -> Asked:
    """What the LLM is asked for: the model the agent named, or the vendor file's own."""
    return Asked(
        settings=settings,
        keys=brought.keys,
        lends=brought.lends,
        model=config.llm.model if config.llm else None,
    )


# The language reaches the ears and the voice as its primary subtag, `es` for `es-ES`, `en_US` or
# `spanish` alike: an agent declares it as a free string and the plugins file voices and hints
# under the base code, so a tag that went through verbatim reached Cartesia and Deepgram as a
# language they do not list (2026-09-26). providers/language.py is the one reading of it.
def _hearing(config: AgentConfig, settings: Settings, brought: Brought) -> Asked:
    """What the STT is asked for: the model, the language, the words, and what ends a turn."""
    return Asked(
        settings=settings,
        keys=brought.keys,
        lends=brought.lends,
        model=config.stt.model if config.stt else None,
        language=primary(config.language),
        endpointing_ms=config.turn.endpointing_ms if config.turn else None,
        eot_threshold=config.turn.eot_threshold if config.turn else None,
        eager_eot_threshold=config.turn.eager_eot_threshold if config.turn else None,
        hears=config.hears,
    )


# The voice arrives resolved — the gateway turned the tenant's word into a vendor id when the app
# declared itself — so the only thing left to decide here is the language a curated voice was
# chosen for, which an agent that declared none of its own inherits.
def _speaking(config: AgentConfig, settings: Settings, brought: Brought) -> Asked:
    """What the TTS is asked for: which model speaks, in which voice, in which language."""
    voice = config.voice
    return Asked(
        settings=settings,
        keys=brought.keys,
        lends=brought.lends,
        model=voice.model if voice else None,
        language=primary(config.language or (curated_voices.language_of(voice) if voice else None)),
        voice_id=voice.voice_id if voice else None,
    )


# The settings door's question, asked of the three an agent would run — the vendor it named or
# ours, the model it named or that vendor's default — with the rule a call is built under.
def first_unlent_vendor(config: AgentConfig, brought: Brought) -> str | None:
    """The first of its llm, ears and voice the box would not run for this org, said; else None."""
    stages = (
        (LLM_VENDORS, config.llm, DEFAULT_VENDOR),
        (STT_VENDORS, config.stt, DEFAULT_STT),
        (TTS_VENDORS, config.voice, DEFAULT_TTS),
    )
    for vendors, asked, ours in stages:
        model = asked.model if asked is not None and asked.model else None
        said = vendors.refusal_to_run(
            vendor_running(asked, ours), model, brought.keys, brought.lends
        )
        if said is not None:
            return said
    return None


# Public and pure, because the console's pipeline door asks the same question about an agent that
# is not on a call: what a session WOULD be built with is the only honest thing to put on a screen.
def vendor_running(asked: Model | Voice | None, ours: str) -> str:
    """The vendor the agent named for one modality, or ours when it named none."""
    return asked.provider if asked is not None and asked.provider else ours


# A blank declaration once silenced a whole line of calls, so an undeclared vendor is never quiet:
# the log says what this build chose, by name, before the call starts.
def _vendor(config: AgentConfig, modality: str, asked: Model | Voice | None, ours: str) -> str:
    """The vendor the agent named for one modality, or ours with a warning that names it."""
    if asked is None or not asked.provider:
        logger.warning("agent %s declared no %s vendor; running %s", config.slug, modality, ours)
    return vendor_running(asked, ours)
