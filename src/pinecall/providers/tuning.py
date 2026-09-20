"""What the org set, laid over what the app declared: the config the next session is built on."""

from __future__ import annotations

import dataclasses
from typing import Any

from pinecall.providers import catalog
from pinecall.providers.llm import VENDORS as LLM_VENDORS
from pinecall.providers.models import DEFAULT_VENDOR
from pinecall.providers.pipeline import DEFAULT_STT, DEFAULT_TTS
from pinecall.providers.registry import NoProvider, Vendors
from pinecall.providers.stt import VENDORS as STT_VENDORS
from pinecall.providers.tts import VENDORS as TTS_VENDORS
from pinecall.providers.tts.elevenlabs import a_model
from pinecall.providers.tts.voices import voice_declared
from pinecall.types import AgentConfig, DeclarationRefused, Lexicon, Model, Tuning, Voice

# The vendor tables' own refusal, over the vendor tables' own list. The list is forty-nine long
# now, so the sentence names the door that prints it rather than printing it into a form's error.
NO_VENDOR = (
    "no {modality} vendor named {vendor!r}; this build has {count} of them — "
    "GET /v1/providers lists every one, with the other words each answers to"
)

# providers/tts/elevenlabs.py substitutes these models and warns the log; a person typing one into
# a form is refused instead, because they are about to press save and believe it. The list itself
# is that file's (INSTEAD and ALLOWED), asked through a_model() and never restated here. It is
# ElevenLabs' rule and only ElevenLabs': a Cartesia model goes to Cartesia unexamined.
NOT_RUN_HERE = "elevenlabs {asked} is not run here; this build speaks with {instead} — ask for that"

# `anthropic/claude-haiku-4-5` names the vendor and the model; `claude-haiku-4-5` alone keeps
# whichever vendor is already in use, and `cartesia` alone names a vendor and keeps its own default
# model. One separator, said once, for the three model knobs.
VENDOR_SEPARATOR = "/"


# The one function every session is built through, whichever door it came in by: the worker's
# config hop, the chat socket, a WhatsApp thread, an eval run. The class declares the contract —
# its tools, its layout, its language — and the world sets the environment, knob by knob; a knob
# the org never set is the runtime's own default. The lexicon is the org's words, the agent's
# `says` and `hears`. Building the config IS the check — what a door accepts is exactly what the
# next session runs, so nothing is validated twice in two places.
def tuned(declared: AgentConfig, tuning: Tuning, lexicon: Lexicon) -> AgentConfig:
    """What the class declared with the world's settings on it: what the next session runs."""
    return dataclasses.replace(
        declared,
        greeting=tuning.greeting,
        voice=_voice(tuning),
        stt=_model("stt", STT_VENDORS, tuning.stt, DEFAULT_STT),
        llm=_model("llm", LLM_VENDORS, tuning.llm, DEFAULT_VENDOR),
        hangup=tuning.hangup,
        turn=tuning.turn,
        memory=tuning.memory,
        knowledge=tuning.knowledge,
        bases=tuning.bases or (),
        says=dict(lexicon.said),
        hears=tuple(lexicon.heard),
    )


# ── one knob at a time ──────────────────────────────────────────────────────────


# A person types the same two forms an app declares — a curated name, or their own vendor id — and
# they go through the same door, providers/tts/voices.py. A form that took a raw id unchecked is
# how `carolina` reached ElevenLabs and closed the line with 1008 seven times.
#
# The vendor comes off `tts` when that knob is set, which is what makes the speaking stage as
# movable as the other two: `tts = "cartesia"` moves the whole stage, and the voice written beside
# it is then Cartesia's own id, because voice_declared takes a named vendor at its word. Nothing
# set is no voice at all: the session speaks with this build's default vendor and its own voice.
def _voice(tuning: Tuning) -> Voice | None:
    """The voice the agent speaks in: the vendor, the id and the model may each be set."""
    if tuning.voice is None and tuning.tts is None and tuning.tts_model is None:
        return None
    vendor, model = _speaking(tuning)
    return Voice(provider=vendor, model=model, voice_id=_voice_id(tuning, vendor))


# `tts` carries the same two forms the other model knobs do, and `tts_model` is the older way to
# say the half after the slash. Both are read, the explicit `tts_model` wins, and a vendor named
# in neither is whatever the app declared.
def _speaking(tuning: Tuning) -> tuple[str, str | None]:
    """Which vendor speaks and with which model, out of the two knobs that can say so."""
    vendor, model = (
        the_vendor_and_the_model(tuning.tts, DEFAULT_TTS) if tuning.tts else (DEFAULT_TTS, "")
    )
    _refuse_an_unknown_vendor("tts", TTS_VENDORS, vendor)
    return vendor, _a_voice_model(vendor, tuning.tts_model or model or None)


def _voice_id(tuning: Tuning, vendor: str) -> str | None:
    """What the vendor is sent: the table's id for the name asked, or nothing when none was."""
    return None if tuning.voice is None else voice_declared(tuning.voice, vendor, None).voice_id


def _model(
    modality: catalog.Modality, vendors: Vendors[Any], asked: str | None, ours: str
) -> Model | None:
    """A model knob, in any of the three forms the_vendor_and_the_model reads; None unset."""
    if asked is None:
        return None
    vendor, model = the_vendor_and_the_model(asked, ours)
    _refuse_an_unknown_vendor(modality, vendors, vendor)
    return Model(provider=vendor, model=model)


# Three forms, one reading, for all three model knobs — and the middle one is what the console's
# vendor picker sends. `anthropic/claude-haiku-4-5` names both. `cartesia` alone names a VENDOR and
# keeps that vendor's own default model. `claude-haiku-4-5` alone names a model on whichever vendor
# is already in use.
#
# A bare word used to be read as a model, always. That was right while this build had five vendors
# and no word was both; with forty-nine there is nothing a person could mean by typing `cartesia`
# except the vendor — and providers/catalog.py keeps an alias from ever being a model name, so the
# two readings cannot collide.
def the_vendor_and_the_model(asked: str, in_use: str) -> tuple[str, str]:
    """Which vendor a knob names and which model, from `vendor/model`, a vendor, or a model."""
    named, _, said = asked.rpartition(VENDOR_SEPARATOR)
    if named:
        return catalog.canonical(named), said
    if catalog.named(said) is not None:
        return catalog.canonical(said), ""
    return in_use, said


# The one vendor this build has an opinion about, and it is only asked about that vendor. Every
# other TTS model is the vendor's own business: providers/plugin.py hands it over and the vendor
# is the one that knows whether it has a model by that name.
def _a_voice_model(vendor: str, wanted: str | None) -> str | None:
    """The tts model, refused here when this build would have quietly spoken with another one."""
    if wanted is None or vendor != "elevenlabs":
        return wanted
    try:
        instead = a_model(wanted)
    except NoProvider as refused:
        raise DeclarationRefused(str(refused)) from refused
    if instead != wanted:
        raise DeclarationRefused(NOT_RUN_HERE.format(asked=wanted, instead=instead))
    return wanted


def _refuse_an_unknown_vendor(modality: str, vendors: Vendors[Any], vendor: str) -> None:
    """A vendor this build has no row for is a typo, and a typo must not reach the next call."""
    if vendor not in vendors.names:
        raise DeclarationRefused(
            NO_VENDOR.format(modality=modality, vendor=vendor, count=len(vendors.names))
        )
