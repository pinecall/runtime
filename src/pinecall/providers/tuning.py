"""What the org set, laid over what the app declared: the config the next session is built on."""

from __future__ import annotations

import dataclasses
from typing import Any

from pinecall.providers import catalog
from pinecall.providers.declaration import a_greeting, a_hangup, a_memory_policy, the_docs
from pinecall.providers.llm import VENDORS as LLM_VENDORS
from pinecall.providers.models import DEFAULT_VENDOR
from pinecall.providers.pipeline import DEFAULT_STT, DEFAULT_TTS, vendor_running
from pinecall.providers.registry import NoProvider, Vendors
from pinecall.providers.stt import VENDORS as STT_VENDORS
from pinecall.providers.tts import VENDORS as TTS_VENDORS
from pinecall.providers.tts.elevenlabs import a_model
from pinecall.providers.tts.voices import voice_declared
from pinecall.types import AgentConfig, DeclarationRefused, Greeting, Lexicon, Model, Tuning, Voice
from pinecall_protocol import defs

# The vendor tables' own refusal, over the vendor tables' own list. The list is forty-five long
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
# config hop, the chat socket, a WhatsApp thread, an eval run. What the org set wins over what the
# app declared, knob by knob; a knob the org never set leaves the declaration exactly as it was.
# The lexicon is MERGED, not laid over: an agent's own pronunciations stay, and the org's word wins
# where both say the same one. Building the config IS the check — what a door accepts is exactly
# what the next session runs, so nothing is validated twice in two places.
def tuned(declared: AgentConfig, tuning: Tuning, lexicon: Lexicon) -> AgentConfig:
    """What the app declared with the org's tuning laid over it: what the next session runs."""
    return dataclasses.replace(
        declared,
        greeting=_greeting(tuning.greeting, declared.greeting),
        voice=_voice(tuning, declared.voice),
        stt=_model("stt", STT_VENDORS, tuning.stt, declared.stt, DEFAULT_STT),
        llm=_model("llm", LLM_VENDORS, tuning.llm, declared.llm, DEFAULT_VENDOR),
        hangup=declared.hangup if tuning.hangup is None else tuning.hangup,
        turn=declared.turn if tuning.turn is None else tuning.turn,
        memory=declared.memory if tuning.memory is None else tuning.memory,
        # Every base the world attached, and the first of them as `docs`, for the one place that
        # asks whether the model has a search tool at all (session/lookups.py).
        docs=tuning.knowledge[0] if tuning.knowledge else declared.docs,
        bases=tuning.knowledge if tuning.knowledge else ((declared.docs,) if declared.docs else ()),
        says={**declared.says, **lexicon.said},
        hears=tuple(dict.fromkeys((*declared.hears, *lexicon.heard))),
    )


# What a class still declares of the environment — a voice, the models, an opening, what it
# remembers, the base it reads — as the tuning a world with nothing set is seeded with, once: the
# first `pinecall start` of any developer gives the team's sandbox its v1, and the box's own app
# gives production its. Read off the WIRE and not the resolved config, because the wire still
# carries the voice's name and the resolved config only its id. None when the class declares
# nothing of it, which is what a class written for the world to own looks like.
def declared_as_tuning(wire: defs.AgentConfig) -> Tuning | None:
    """The environment a class still declares, as a tuning; None when it declares none."""
    voice = wire.voice
    seed = Tuning(
        voice=None if voice is None else (voice.name or voice.voice_id),
        tts=None if voice is None or voice.name is not None else voice.provider,
        tts_model=None if voice is None else voice.model,
        stt=_a_model_knob(wire.stt),
        llm=_a_model_knob(wire.llm),
        greeting=a_greeting(wire.greeting),
        hangup=a_hangup(wire.hangup),
        memory=a_memory_policy(wire.memory),
        knowledge=() if (docs := the_docs(wire.docs)) is None else (docs,),
    )
    return None if seed == Tuning() else seed


def _a_model_knob(wire: defs.ModelConfig | None) -> str | None:
    """`vendor/model`, or the vendor alone when the app named none: the knob's own three forms."""
    if wire is None:
        return None
    return f"{wire.provider}/{wire.model}" if wire.model else wire.provider


# ── one knob at a time ──────────────────────────────────────────────────────────


# An opening the org set is the opening — the words, or what the model reads before it finds its
# own. Whether the caller may interrupt it stays the class's unless the org said otherwise: turning
# the words is not turning the interruptibility, and a form that set one never set the other.
def _greeting(turned: Greeting | None, declared: Greeting | None) -> Greeting | None:
    """The opening: the org's when it set one, the class's when it did not."""
    if turned is None:
        return declared
    if turned.allow_interruptions is not None or declared is None:
        return turned
    return Greeting(
        say=turned.say, reply=turned.reply, allow_interruptions=declared.allow_interruptions
    )


# A person types the same two forms an app declares — a curated name, or their own vendor id — and
# they go through the same door, providers/tts/voices.py. A form that took a raw id unchecked is
# how `carolina` reached ElevenLabs and closed the line with 1008 seven times.
#
# The vendor comes off `tts` when that knob is set, which is what makes the speaking stage as
# movable as the other two: `tts = "cartesia"` moves the whole stage, and the voice written beside
# it is then Cartesia's own id, because voice_declared takes a named vendor at its word.
def _voice(tuning: Tuning, declared: Voice | None) -> Voice | None:
    """The voice the agent speaks in: the vendor, the id and the model may each be set."""
    if tuning.voice is None and tuning.tts is None and tuning.tts_model is None:
        return declared
    vendor, model = _speaking(tuning, declared)
    return Voice(
        provider=vendor,
        model=model or (declared.model if declared else None),
        voice_id=_voice_id(tuning, vendor, declared),
    )


# `tts` carries the same two forms the other model knobs do, and `tts_model` is the older way to
# say the half after the slash. Both are read, the explicit `tts_model` wins, and a vendor named
# in neither is whatever the app declared.
def _speaking(tuning: Tuning, declared: Voice | None) -> tuple[str, str | None]:
    """Which vendor speaks and with which model, out of the two knobs that can say so."""
    in_use = vendor_running(declared, DEFAULT_TTS)
    vendor, model = the_vendor_and_the_model(tuning.tts, in_use) if tuning.tts else (in_use, "")
    _refuse_an_unknown_vendor("tts", TTS_VENDORS, vendor)
    return vendor, _a_voice_model(vendor, tuning.tts_model or model or None)


def _voice_id(tuning: Tuning, vendor: str, declared: Voice | None) -> str | None:
    """What the vendor is sent: the table's id for the name asked, or what the app declared."""
    if tuning.voice is None:
        return declared.voice_id if declared else None
    return voice_declared(tuning.voice, vendor, None).voice_id


def _model(
    modality: catalog.Modality,
    vendors: Vendors[Any],
    asked: str | None,
    declared: Model | None,
    ours: str,
) -> Model | None:
    """A model knob, in any of the three forms the_vendor_and_the_model reads."""
    if asked is None:
        return declared
    vendor, model = the_vendor_and_the_model(asked, vendor_running(declared, ours))
    _refuse_an_unknown_vendor(modality, vendors, vendor)
    temperature = declared.temperature if declared else None
    return Model(provider=vendor, model=model, temperature=temperature)


# Three forms, one reading, for all three model knobs — and the middle one is what the console's
# vendor picker sends. `anthropic/claude-haiku-4-5` names both. `cartesia` alone names a VENDOR and
# keeps that vendor's own default model. `claude-haiku-4-5` alone names a model on whichever vendor
# is already in use.
#
# A bare word used to be read as a model, always. That was right while this build had five vendors
# and no word was both; with forty-five there is nothing a person could mean by typing `cartesia`
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
