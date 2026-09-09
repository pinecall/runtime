"""The table a modality keeps: a vendor is one file, and the file registers itself in one line."""

from __future__ import annotations

import pkgutil
from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module
from types import MappingProxyType
from typing import Any, cast

from livekit.agents import llm, stt, tts

from pinecall._exceptions import PinecallError
from pinecall._settings import Settings
from pinecall.types import ProviderKeys

# livekit's LLM, STT and TTS are each generic over the extra events a plugin may emit; nothing here
# listens to one, so the runtime names them once with these and never spells the parameter again.
type Chat = llm.LLM[Any]
type Ears = stt.STT[Any]
type Speech = tts.TTS[Any]


class NoProvider(PinecallError):
    """The agent asked for a vendor this build has no file for, or no key to reach it."""


# Which settings field a vendor reads its key from when the org brought none. The vendor files
# each read exactly one, and elevenlabs is the one whose variable is not its own name — so the
# pairs are written here, once, and no vendor file knows a settings field exists.
KEY_OF: dict[str, str] = {
    "anthropic": "anthropic_api_key",
    "deepgram": "deepgram_api_key",
    "elevenlabs": "eleven_api_key",
    "openai": "openai_api_key",
    "soniox": "soniox_api_key",
    "whatsapp": "whatsapp_access_token",
}

# A vendor with no key is a refusal at the start of the call, and never a 401 in the middle of a
# caller's turn. The gateway's pipeline screen says the same sentence before the call, so a person
# sees the missing key as a state (gateway/pipeline/report.py).
NO_KEY = "{vendor} has no API key in this process"

# The org that brought none of its own, which is every org on a box running managed keys.
NO_ORG_KEYS: ProviderKeys = MappingProxyType({})


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
    """One modality's vendors, found by importing its package: name in, livekit object out."""

    def __init__(self, modality: str, package: str) -> None:
        self._modality = modality
        self._package = package
        self._rows: dict[str, Build[Made]] = {}
        self._read = False

    # The one line a new vendor file writes above its build function. Nothing else changes: the
    # package is read whole, so the file being there IS the registration.
    def registers(self, vendor: str) -> Callable[[Build[Made]], Build[Made]]:
        """Claim a vendor name for the function underneath: the whole of a file's bookkeeping."""

        def keep(build: Build[Made]) -> Build[Made]:
            self._rows[vendor] = build
            return build

        return keep

    @property
    def names(self) -> tuple[str, ...]:
        """Every vendor this build reaches for this modality, in the order a refusal lists them."""
        self.read()
        return tuple(sorted(self._rows))

    def read(self) -> None:
        """Fill the table by importing the package, once. Idempotent, and safe before any job."""
        self._read_the_package()

    def build(self, vendor: str, asked: Asked) -> Made:
        """The object that vendor makes, or a refusal naming every vendor this build does have."""
        self._read_the_package()
        row = self._rows.get(vendor)
        if row is None:
            known = ", ".join(sorted(self._rows)) or "no vendor at all"
            raise NoProvider(f"no {self._modality} vendor named {vendor!r}; this build has {known}")
        return row(asked)

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
        """Import every file of the modality's package, so a new vendor needs no other edit."""
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


def _the_boxes_key(vendor: str, settings: Settings) -> str | None:
    """What the environment holds for the vendor, under the vendor's own variable name."""
    field_name = KEY_OF.get(vendor)
    return None if field_name is None else cast("str | None", getattr(settings, field_name))
