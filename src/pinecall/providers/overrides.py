"""What an operator turned at the pipeline door, held per process, over what the app declared."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any, Protocol

from pinecall.providers.llm import VENDORS as LLM_VENDORS
from pinecall.providers.models import DEFAULT_VENDOR
from pinecall.providers.pipeline import DEFAULT_STT, DEFAULT_TTS, vendor_running
from pinecall.providers.registry import NoProvider, Vendors
from pinecall.providers.stt import VENDORS as STT_VENDORS
from pinecall.providers.tts.elevenlabs import a_model
from pinecall.providers.tts.voices import voice_declared
from pinecall.types import AgentConfig, DeclarationRefused, Model, Voice
from pinecall_protocol import WireModel

# convo ms-14: an empty voice reached the vendor and a whole line of calls went out silent, because
# "" is a value and None is not. A knob an operator wants back the way the app declared it is LEFT
# OUT of the body — this door replaces the whole set — and never sent as an empty string.
BLANK = (
    "a blank {field} is not a change: an empty value once silenced a whole line of calls. "
    "Leave the field out to go back to what the app declared."
)

# The vendor tables' own refusal, over the vendor tables' own list: a new vendor file changes this
# sentence with no edit here.
NO_VENDOR = "no {modality} vendor named {vendor!r}; this build has {known}"

# providers/tts/elevenlabs.py substitutes these models and warns the log; a person typing one into
# a form is refused instead, because they are about to press save and believe it. The list itself
# is that file's (INSTEAD and ALLOWED), asked through a_model() and never restated here.
NOT_RUN_HERE = "elevenlabs {asked} is not run here; this build speaks with {instead} — ask for that"

# `anthropic/claude-haiku-4-5` names the vendor and the model; `claude-haiku-4-5` alone keeps
# whichever vendor is already in use. One separator, said once, for the two model knobs.
VENDOR_SEPARATOR = "/"


class Overridden(WireModel):
    """The five knobs an operator may turn. A knob left out is not overridden at all."""

    voice: str | None = None
    tts_model: str | None = None
    stt: str | None = None
    llm: str | None = None
    greeting: str | None = None

    def checked(self, config: AgentConfig) -> Overridden:
        """Every rule this set must pass, run before a single call is built from it."""
        self._refuse_a_blank()
        # Building the config the worker would be handed IS the check: what the door accepts is
        # exactly what the next session runs, so nothing is validated twice in two places.
        self.applied_to(config)
        return self

    def applied_to(self, config: AgentConfig) -> AgentConfig:
        """What the app declared with these knobs turned: what the next session is built on."""
        return dataclasses.replace(
            config,
            greeting=self.greeting or config.greeting,
            voice=self._voice(config.voice),
            stt=self._model("stt", STT_VENDORS, self.stt, config.stt, DEFAULT_STT),
            llm=self._model("llm", LLM_VENDORS, self.llm, config.llm, DEFAULT_VENDOR),
        )

    # ── one knob at a time ──────────────────────────────────────────────────────

    def _refuse_a_blank(self) -> None:
        """A knob that is there but empty is the bug this door exists to refuse."""
        for field, value in self.model_dump().items():
            if value is not None and not str(value).strip():
                raise DeclarationRefused(BLANK.format(field=field))

    # An operator types the same two forms an app declares — a curated name, or their own vendor
    # id — and they go through the same door, providers/tts/voices.py. A form that took a raw id
    # unchecked is how `carolina` reached ElevenLabs and closed the line with 1008 seven times.
    def _voice(self, declared: Voice | None) -> Voice | None:
        """The voice the agent speaks in: the vendor stays, the id and the model may be turned."""
        if self.voice is None and self.tts_model is None:
            return declared
        vendor = vendor_running(declared, DEFAULT_TTS)
        return Voice(
            provider=vendor,
            model=_a_voice_model(self.tts_model) or (declared.model if declared else None),
            voice_id=self._voice_id(vendor, declared),
        )

    def _voice_id(self, vendor: str, declared: Voice | None) -> str | None:
        """What the vendor is sent: the table's id for the name asked, or what the app declared."""
        if self.voice is None:
            return declared.voice_id if declared else None
        return voice_declared(self.voice, vendor, None).voice_id

    def _model(
        self,
        modality: str,
        vendors: Vendors[Any],
        asked: str | None,
        declared: Model | None,
        ours: str,
    ) -> Model | None:
        """A model knob: `vendor/model`, or a bare model that keeps the vendor already in use."""
        if asked is None:
            return declared
        named, _, model = asked.rpartition(VENDOR_SEPARATOR)
        vendor = named or vendor_running(declared, ours)
        _refuse_an_unknown_vendor(modality, vendors, vendor)
        temperature = declared.temperature if declared else None
        return Model(provider=vendor, model=model, temperature=temperature)


def _a_voice_model(wanted: str | None) -> str | None:
    """The tts model, refused here when this build would have quietly spoken with another one."""
    if wanted is None:
        return None
    try:
        instead = a_model(wanted)
    except NoProvider as refused:
        raise DeclarationRefused(str(refused)) from refused
    if instead != wanted:
        raise DeclarationRefused(NOT_RUN_HERE.format(asked=wanted, instead=instead))
    return wanted


def _refuse_an_unknown_vendor(modality: str, vendors: Vendors[Any], vendor: str) -> None:
    """A vendor this build has no file for is a typo, and a typo must not reach the next call."""
    if vendor not in vendors.names:
        known = ", ".join(vendors.names) or "no vendor at all"
        raise DeclarationRefused(NO_VENDOR.format(modality=modality, vendor=vendor, known=known))


class Keeps(Protocol):
    """Where a turned set survives a process. Satisfied structurally by orgs/turned.py, which is
    below no line this package may cross: it stores columns and this module gives them meaning."""

    async def all(self) -> Mapping[str, Mapping[str, str | None]]:
        """Every agent that has a knob turned, by slug, each as its own columns."""
        ...

    async def put(self, org: str, agent: str, knobs: Mapping[str, str | None]) -> None:
        """This agent's whole set, replaced whole: a knob left out stops being overridden."""
        ...


class Overrides:
    """This process's memory of the turned knobs, by agent. Held on app.state, never globally."""

    def __init__(self, kept: Keeps | None = None) -> None:
        self._kept = kept
        self._by_agent: dict[str, Overridden] = {}

    # Read once, when the process starts, and never again: a gateway holds the agents whose sockets
    # it answers, so the table is small and the read is one query. What a second gateway turns is
    # that gateway's until this one restarts, which is already true of the registry a slug lives in.
    async def loaded(self) -> None:
        """Every knob any operator has ever turned, from the table, into this process's memory."""
        if self._kept is not None:
            self._by_agent = {
                agent: Overridden.model_validate(dict(knobs))
                for agent, knobs in (await self._kept.all()).items()
            }

    def of(self, agent: str) -> Overridden:
        """What is turned for this agent; every knob None until somebody turns one."""
        return self._by_agent.get(agent, Overridden())

    async def set(self, org: str, agent: str, turned: Overridden) -> None:
        """Replace this agent's whole set: a knob left out of the body stops being overridden."""
        self._by_agent[agent] = turned
        if self._kept is not None:
            await self._kept.put(org, agent, turned.model_dump(mode="json"))

    # The one place a config is read for a session, whichever door the session came through: the
    # worker's config hop and the chat socket both build from this, so neither can skip a knob.
    def config_for(self, agent: str, declared: AgentConfig) -> AgentConfig:
        """What the next session of this agent is built on: what the app declared, knobs turned."""
        return self.of(agent).applied_to(declared)
