"""The table a modality keeps: a tuned vendor is one file, and every other one is the catalog."""

from __future__ import annotations

import pkgutil
from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module
from typing import Any, cast

from livekit.agents import llm, stt, tts

from pinecall._exceptions import PinecallError
from pinecall._settings import Settings
from pinecall.providers import catalog
from pinecall.providers.catalog import Modality, Provider
from pinecall.providers.plugin import (
    NOT_INSTALLED,
    PluginProblem,
    built_by_a_plugin,
    installed,
)
from pinecall.types import NO_ORG_KEYS, ProviderKeys

# livekit's LLM, STT and TTS are each generic over the extra events a plugin may emit; nothing here
# listens to one, so the runtime names them once with these and never spells the parameter again.
type Chat = llm.LLM[Any]
type Ears = stt.STT[Any]
type Speech = tts.TTS[Any]


class NoProvider(PinecallError):
    """The agent asked for a vendor nobody catalogues, or one this build has no key to reach."""


# A vendor with no key is a refusal at the start of the call, and never a 401 in the middle of a
# caller's turn. The gateway's pipeline screen says the same sentence before the call, so a person
# sees the missing key as a state (api/pipeline_report.py).
NO_KEY = "{vendor} has no API key in this process"

# What a word nobody catalogues reads as. The list is long now — forty-five vendors — so the
# sentence names the door that prints the whole of it instead of printing it into a log line.
NO_VENDOR = (
    "no {modality} vendor named {vendor!r}; this build catalogues {count} of them "
    "(GET /v1/providers lists every one, with its aliases)"
)


# Every modality asks the same question in different words, so one shape carries all of them and
# each vendor file reads the two or three fields it actually knows about.
@dataclass(frozen=True)
class Asked:
    """What one modality was asked for: which model, which voice, which words, whose keys."""

    settings: Settings
    model: str | None = None
    language: str | None = None
    voice_id: str | None = None
    endpointing_ms: int | None = None
    hears: tuple[str, ...] = ()
    # The org's own keys, when it brought any. Empty is the common case and means the box's.
    keys: ProviderKeys = NO_ORG_KEYS


type Build[Made] = Callable[[Asked], Made]
"""What a vendor file writes: one function, one vendor, one object the session can use."""


class Vendors[Made]:
    """One modality's vendors: the tuned files, and behind them the whole catalog."""

    def __init__(self, modality: Modality, package: str) -> None:
        self._modality: Modality = modality
        self._package = package
        self._rows: dict[str, Build[Made]] = {}
        self._models: dict[str, tuple[str, ...]] = {}
        self._read = False

    # The one line a tuned vendor file writes above its build function. Nothing else changes: the
    # package is read whole, so the file being there IS the registration. `models` is what this
    # build vouches for at that vendor, the default first: a screen offers these as a list, so a
    # person picks a model that exists instead of typing one that does not (`anthropic/haiku` was
    # a dead agent with no sound about it). A vendor with none reads as "its own default".
    def registers(
        self, vendor: str, *, models: tuple[str, ...] = ()
    ) -> Callable[[Build[Made]], Build[Made]]:
        """Claim a vendor name for the function underneath: the whole of a file's bookkeeping."""

        def keep(build: Build[Made]) -> Build[Made]:
            self._rows[vendor] = build
            self._models[vendor] = models
            return build

        return keep

    def models(self, vendor: str) -> tuple[str, ...]:
        """The models this build vouches for at a vendor, the default first; none when unsaid."""
        self.read()
        return self._models.get(catalog.canonical(vendor), ())

    @property
    def names(self) -> tuple[str, ...]:
        """Every vendor this modality reaches: the tuned files and the catalogued rest, sorted."""
        self.read()
        catalogued = {row.name for row in catalog.doing(self._modality)}
        return tuple(sorted(catalogued | set(self._rows)))

    @property
    def tuned(self) -> tuple[str, ...]:
        """The vendors with a file of their own here: the ones this runtime has an opinion about."""
        self.read()
        return tuple(sorted(self._rows))

    def read(self) -> None:
        """Fill the table by importing the package, once. Idempotent, and safe before any job."""
        self._read_the_package()

    def build(self, vendor: str, asked: Asked) -> Made:
        """The object that vendor makes: its tuned file, its plugin, or a refusal naming neither."""
        self._read_the_package()
        row = self._rows.get(catalog.canonical(vendor))
        if row is not None:
            return row(asked)
        return cast("Made", self._out_of_the_catalog(vendor, asked))

    # The catalogued path. It reaches every vendor livekit ships a plugin for, with no file here
    # and no edit when livekit adds one — providers/plugin.py reads the plugin's own signature.
    def _out_of_the_catalog(self, vendor: str, asked: Asked) -> Any:
        """One vendor out of its plugin, or the refusal that names what this build does have."""
        provider = catalog.named(vendor)
        if provider is None or self._modality not in provider.does:
            raise NoProvider(
                NO_VENDOR.format(
                    modality=self._modality,
                    vendor=vendor,
                    count=len(catalog.doing(self._modality)),
                )
            )
        # Before the key and not after it: a vendor this build has no plugin for would otherwise be
        # refused for a missing key, and the operator would go and fetch one that changes nothing.
        if not installed(provider):
            raise NoProvider(NOT_INSTALLED.format(vendor=provider.name, extra=provider.extra))
        try:
            return built_by_a_plugin(
                self._modality,
                provider,
                key=_a_key_if_it_has_one(provider, asked),
                model=asked.model,
                voice_id=asked.voice_id,
                language=asked.language,
            )
        except PluginProblem as problem:
            # One class of refusal above this line, whichever half of the plugin path said no: a
            # door renders NoProvider, and nothing above providers/ knows there are two.
            raise NoProvider(str(problem)) from problem

    # The table is filled once, by import, and only read afterwards — a constant that happens to be
    # assembled rather than typed out. Nothing mutates it while a call is running.
    #
    # "Once" means once SUCCESSFULLY, and the flag is set after the imports for that reason: a
    # package that raises — a plugin that wants the main thread, a half-installed extra, a missing
    # shared library — leaves it False, so the next caller tries the import again and the raise
    # reaches whoever asked instead of leaving a table that answers nothing for the life of the
    # process. Reading twice is safe: sys.modules keeps the files that did import, a file that
    # raised is dropped from it and runs again, and a row is keyed by vendor name, so a decorator
    # that runs twice writes the same row twice. No lock: import itself is locked per module by
    # importlib, so a second reader waits or finds the module cached, and either way it registers
    # the same rows.
    def _read_the_package(self) -> None:
        """Import every tuned file of the modality's package, so one needs no other edit."""
        if self._read:
            return
        package = import_module(self._package)
        for module in pkgutil.iter_modules(package.__path__):
            import_module(f"{self._package}.{module.name}")
        self._read = True


# The whole of managed-versus-BYOK, in three lines: the org's own key when it brought one for this
# vendor, the box's environment otherwise, and a refusal when neither exists. Nothing above this
# function knows which of the two it got. See docs/decisions/provider-keys.md.
def a_key(vendor: str, asked: Asked) -> str:
    """The key this call runs the vendor with: the org's own, or the box's, or a refusal."""
    key = asked.keys.get(vendor) or _the_boxes_key(vendor, asked.settings)
    if not key:
        raise NoProvider(NO_KEY.format(vendor=vendor))
    return key


# Two vendors have no one key to ask for. AWS authenticates off its own credential chain and
# Google's speech pair off a service account file, so there is no string an org could bring and
# none this function could refuse the absence of: the plugin reads its own environment and either
# the box set that up or the vendor says so. Every other vendor goes through a_key and is refused
# before the call rather than mid-turn.
def _a_key_if_it_has_one(provider: Provider, asked: Asked) -> str | None:
    """The key the plugin is handed, or None for a vendor whose credentials are its own affair."""
    return None if provider.env is None else a_key(provider.name, asked)


# The field is the vendor's own variable name, lowercased — providers/catalog.py holds the rule and
# _vendor_keys.py is written to keep it true, so adding a vendor adds no row to any table here.
def _the_boxes_key(vendor: str, settings: Settings) -> str | None:
    """What the environment holds for the vendor, under the vendor's own variable name."""
    field_name = catalog.settings_field_of(vendor)
    return None if field_name is None else cast("str | None", getattr(settings, field_name, None))
