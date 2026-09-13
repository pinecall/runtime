"""Every vendor this build can run, with what each one needs before it can: one door, one shape."""

# The catalog is not a secret and this door is not a key door: what it answers is which vendors
# exist, which of them this box has the plugin installed for, and whether a key is present — never
# a key, not even a prefix. api/provider_keys.py is still the only place a key goes in, and no door
# anywhere reads one back out.
#
# Two screens need this list and they hold different scopes, so it is answered twice from one
# builder: here, for the Providers screen, and inside the pipeline report, for the screen that
# turns a stage onto another vendor. One `rows()`, so the two can never disagree.

from __future__ import annotations

from fastapi import APIRouter

from pinecall._settings import Settings
from pinecall.api._deps import ProviderKeysKeyDep, SettingsDep
from pinecall.providers import catalog
from pinecall.providers.catalog import MODALITIES, Provider
from pinecall.providers.models import DEFAULT_VENDOR
from pinecall.providers.pipeline import DEFAULT_STT, DEFAULT_TTS
from pinecall.providers.standing import READY, Standing, standing
from pinecall.providers.tts.voices import voice_names
from pinecall_protocol import WireModel

router = APIRouter()


class ProviderRow(WireModel):
    """One vendor as a screen needs it: what it does, what it is called, and what it still wants."""

    name: str
    does: list[str]
    aliases: list[str]
    note: str
    # What this vendor is waiting for on THIS box, in one word, decided by providers/standing.py so
    # that no screen has to combine booleans of its own and get RTZR wrong. A worker is where a
    # call is actually built, so on a split box the plugin half answers for the gateway — which is
    # the machine an operator is looking at. An org's own key is never read here: this door answers
    # the question an operator is asking, which is what the machine in front of them has.
    standing: Standing
    ready: bool
    # The variable a key for this vendor goes under, so a screen can print the one word to set.
    # None: this vendor brings its own credentials and there is nothing to bring.
    env: str | None
    # What installs the plugin, for the vendors this build has no plugin for yet. Empty: nothing
    # to install — `livekit` is livekit-agents itself, `whatsapp` is not a plugin at all.
    extra: str


class Catalogue(WireModel):
    """The whole screen in one answer: the vendors, what runs when nobody chooses, the voices."""

    providers: list[ProviderRow]
    # Which vendor each stage runs on when an agent declares none. A screen shows these as the
    # chosen row rather than leaving three stages looking unconfigured.
    defaults: dict[str, str]
    # The voices this build curates by name, off providers/tts/voices.py. Every other voice is a
    # vendor's own id, and for a vendor that was named that is exactly what a word is taken as.
    voices: list[str]


@router.get("/v1/providers")
async def providers(key: ProviderKeysKeyDep, settings: SettingsDep) -> Catalogue:
    """Every vendor this build runs, which are ready on this box, and which want a key first."""
    del key  # the scope IS the check; the catalog is the same for every org
    return catalogue(settings)


def catalogue(settings: Settings) -> Catalogue:
    """The one builder both doors answer from, so the two screens can never disagree."""
    return Catalogue(
        providers=rows(settings),
        defaults={"llm": DEFAULT_VENDOR, "stt": DEFAULT_STT, "tts": DEFAULT_TTS},
        voices=list(voice_names()),
    )


def rows(settings: Settings) -> list[ProviderRow]:
    """Every catalogued vendor, in the catalog's own order, with this box's state beside it."""
    return [_a_row(row, settings) for row in catalog.PROVIDERS if row.does]


def _a_row(row: Provider, settings: Settings) -> ProviderRow:
    where = standing(row, settings)
    return ProviderRow(
        name=row.name,
        does=[modality for modality in MODALITIES if modality in row.does],
        aliases=list(row.aliases),
        note=row.note,
        standing=where,
        ready=where == READY,
        env=row.env,
        extra=row.extra,
    )
