"""Whose key each stage of a call runs on: the org's own runs anything, the box's what it lends."""

import dataclasses
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Literal

from pinecall.domain.agent import AgentConfig, Model, Voice
from pinecall.domain.errors import DeclarationRefused, NotAllowed, NotAvailable
from pinecall.domain.names import Credentials
from pinecall.providers.build import Modality, Running, Vendor, installed, primary
from pinecall.providers.catalog import Providers
from pinecall.providers.declared import SEPARATOR

# yours: the org brought its key. offered: the box holds one and lends it to this org. bring your
# own: installed, and nobody but the org can key it. broken: its plugin does not import.
type Availability = Literal["yours", "offered", "bring your own", "broken"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Keyring:
    """What a call may run on: the org's own credentials, the box's, and what the box lends it."""

    own: Mapping[str, Credentials] = field(default_factory=dict[str, Credentials])
    box: Mapping[str, Credentials] = field(default_factory=dict[str, Credentials])
    # quotas.lends: None lends everything the box holds, empty lends nothing.
    lends: frozenset[str] | None = None


@dataclass(frozen=True)
class Pipeline:
    """The three stages of a voice call, each on the key it runs on."""

    llm: Running
    stt: Running
    tts: Running

    # The gateway keeps these with the call's facts: their usage is the operator's to bill.
    @property
    def lent(self) -> list[str]:
        """The vendors the call may run on the box's key, in stage order, once each."""
        stages = (self.llm, self.stt, self.tts)
        each = (item for stage in stages for item in (stage, *stage.fallbacks))
        return list(dict.fromkeys(item.vendor for item in each if item.lent))


@dataclass(frozen=True)
class Readiness:
    """One vendor as `GET /v1/providers` shows it to an org: what it does, and whose key runs it."""

    name: str
    does: tuple[Modality, ...]
    availability: Availability
    broken: str | None = None


def readiness(installed: Mapping[str, Vendor], keys: Keyring) -> list[Readiness]:
    """Every installed vendor, sorted, and how this org may run it."""
    return [
        Readiness(
            name=vendor.name,
            does=tuple(sorted(vendor.does)),
            availability=_availability_of(vendor, keys),
            broken=vendor.broken,
        )
        for vendor in sorted(installed.values(), key=lambda one: one.name)
    ]


def pipeline(
    config: AgentConfig, configured: Providers, keys: Keyring, failing: frozenset[str] = frozenset()
) -> Pipeline:
    """The stages an agent runs: its vendors or the defaults, each with its key and options."""
    language = primary(config.language)
    llm = thinking(config, configured, keys, failing)
    stt = stage("stt", config.stt, configured, keys, failing)
    tts = stage("tts", config.voice, configured, keys, failing)
    heard = dict.fromkeys(item for item in (language, *configured.hints) if item)
    voice = config.voice.voice_id if config.voice else None
    ears = (
        dataclasses.replace(item, language=language, hints=tuple(heard))
        for item in (stt, *stt.fallbacks)
    )
    # The agent's voice is its vendor's; a vendor that takes over speaks the row's for the language.
    voices = (
        dataclasses.replace(
            item,
            language=language,
            voice=(voice if item is tts else None)
            or configured.voices.get(f"{item.vendor}{SEPARATOR}{language}"),
        )
        for item in (tts, *tts.fallbacks)
    )
    return Pipeline(llm=llm, stt=_over(*ears), tts=_over(*voices))


# A written call runs this stage alone: a call with no voice is not refused for want of one.
def thinking(
    config: AgentConfig, configured: Providers, keys: Keyring, failing: frozenset[str] = frozenset()
) -> Running:
    """The model an agent thinks with, on its key, at the temperature it declared."""
    llm = stage("llm", config.llm, configured, keys, failing)
    if config.llm is None or config.llm.temperature is None:
        return llm
    return dataclasses.replace(llm, options={**llm.options, "temperature": config.llm.temperature})


def running(keys: Keyring, vendor: str, model: str | None) -> Running:
    """The vendor on the org's own key, else on a key the box lends; refused before the call."""
    own = keys.own.get(vendor)
    if own is not None:
        return Running(vendor=vendor, credentials=own, model=model)
    box = keys.box.get(vendor)
    if box is None:
        raise NotAvailable(f"{vendor} has no key: the org brought none and the box holds none")
    if not lent(keys.lends, vendor, model):
        raise NotAllowed(refusal(keys.lends or frozenset(), vendor, model))
    return Running(vendor=vendor, credentials=box, model=model, lent=True)


# A model entry lends its dated snapshots (`anthropic/claude-haiku-4-5` lends
# `claude-haiku-4-5-20251001`), never a sibling; a plugin's own default needs the whole vendor.
def lent(lends: frozenset[str] | None, vendor: str, model: str | None) -> bool:
    """Whether the box lends this org its key for this vendor and model."""
    if lends is None or vendor in lends:
        return True
    if model is None:
        return False
    prefix = f"{vendor}{SEPARATOR}"
    return any(
        model.startswith(entry.removeprefix(prefix)) for entry in lends if entry.startswith(prefix)
    )


def refusal(lends: frozenset[str], vendor: str, model: str | None) -> str:
    """The sentence a refused org reads: what it asked, what it may run, and the other way."""
    params = vendor if model is None else f"{vendor}{SEPARATOR}{model}"
    may = ", ".join(sorted(lends)) if lends else "nothing of the box's"
    return f"{params} is not lent to this org: it may run on {may}, or on a key of its own"


def parse_lending(entries: Iterable[str]) -> frozenset[str]:
    """The entries an operator lends, each a vendor installed here or `vendor/model`."""
    parsed: set[str] = set()
    for entry in entries:
        named, slash, model = entry.strip().partition(SEPARATOR)
        vendor = named.lower()
        if vendor not in installed():
            raise DeclarationRefused(f"{entry!r} lends no vendor installed here")
        if slash and not model:
            raise DeclarationRefused(f"{entry!r} names a vendor and no model after the slash")
        parsed.add(f"{vendor}{SEPARATOR}{model}" if slash else vendor)
    return frozenset(parsed)


# An agent that names its vendor runs it alone; one on the row's default runs the default's
# fallbacks behind it, each on its own key. A fallback this org cannot key is left out. The row's
# order stands but for the vendors `failing` names (over their error line now), which go last,
# among themselves in the row's order too.
def stage(
    modality: Modality,
    declared: Model | Voice | None,
    configured: Providers,
    keys: Keyring,
    failing: frozenset[str] = frozenset(),
) -> Running:
    """One stage on its key: the vendor declared or the default, with the operator's options."""
    if declared is not None:
        return _on_its_key(modality, declared.provider, declared.model or None, configured, keys)
    default = configured.defaults[modality]
    chosen = _on_its_key(modality, default.vendor, default.model, configured, keys)
    backups: list[Running] = []
    for fallback in default.fallbacks:
        try:
            backups.append(_on_its_key(modality, fallback.vendor, fallback.model, configured, keys))
        except (NotAvailable, NotAllowed):
            logger.info(
                "%s fallback %s has no key this org may run it on", modality, fallback.vendor
            )
    return _over(*sorted((chosen, *backups), key=lambda item: item.vendor in failing))


def _on_its_key(
    modality: Modality, vendor: str, params: str | None, configured: Providers, keys: Keyring
) -> Running:
    model = params or configured.models.get(f"{modality}{SEPARATOR}{vendor}")
    chosen = running(keys, vendor, model)
    options = configured.tuning.get(f"{modality}{SEPARATOR}{vendor}")
    if options is None:
        return chosen
    return dataclasses.replace(
        chosen,
        builds=options.builds,
        options=dict(options.options),
        ends_the_turn=options.ends_the_turn,
        turn_model=options.turn_model,
    )


def _over(primary: Running, *fallbacks: Running) -> Running:
    return dataclasses.replace(primary, fallbacks=fallbacks)


def _availability_of(vendor: Vendor, keys: Keyring) -> Availability:
    if vendor.broken is not None:
        return "broken"
    if vendor.name in keys.own:
        return "yours"
    if vendor.name in keys.box and lent(keys.lends, vendor.name, None):
        return "offered"
    return "bring your own"
