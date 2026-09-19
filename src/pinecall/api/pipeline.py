"""The two doors of an agent's pipeline: what it runs on, and the six knobs, kept one release."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from pinecall.api._deps import PipelineKeyDep, SettingsDep, StoreDep, TuningDep
from pinecall.api.agents.registry import NO_AGENT, RegistryDep
from pinecall.api.pipeline_report import Overridden, Report, report, turned
from pinecall.auth.corner import author_of, whose_corner
from pinecall.auth.keys import KeyRecord, held_by
from pinecall.orgs.tuning import TuningStore
from pinecall.providers.tuning import tuned
from pinecall.types import AgentConfig, DeclarationRefused, Lexicon, Tuning

router = APIRouter()

# The note every version this door writes carries: a reader of the history knows it came through
# the six-knob door and not the settings one.
THROUGH_THE_KNOBS = "pipeline/overrides"


@router.get("/v1/agents/{slug}/pipeline")
async def pipeline(
    slug: str,
    key: PipelineKeyDep,
    registry: RegistryDep,
    kept: TuningDep,
    store: StoreDep,
    settings: SettingsDep,
) -> Report:
    """What this agent hears, decides and speaks with, what it measured, and what is set."""
    declared = declared_here(slug, key, registry)
    tuning, lexicon = await standing(kept, key, slug)
    return await report(slug, declared, tuning, lexicon, store, settings)


# Kept one release for the console's Pipeline screen, which PUTs here until the settings screen
# replaces it. The body is still the whole set of six: a knob left out is not set, as it always
# was. What it writes is the next version of the key's corner's tuning — the six replaced, the
# rest (the cut of a turn, what is remembered, the bases) kept as they stood — through the same
# store the settings door writes, so nothing here is a second truth. On a production key it
# writes production, as the old table did; the settings door refuses that, and this one goes.
@router.put("/v1/agents/{slug}/pipeline/overrides")
async def turn(
    slug: str,
    knobs: Overridden,
    key: PipelineKeyDep,
    registry: RegistryDep,
    kept: TuningDep,
    store: StoreDep,
    settings: SettingsDep,
) -> Report:
    """Turn the knobs. Refused whole or applied whole, and the next session is built with them."""
    declared = declared_here(slug, key, registry)
    tuning, lexicon = await standing(kept, key, slug)
    try:
        wanted = turned(tuning, knobs)
        tuned(declared, wanted, lexicon)
    except DeclarationRefused as refused:
        raise HTTPException(400, str(refused)) from refused
    await kept.put(
        key.org,
        key.env,
        whose_corner(key),
        slug,
        wanted,
        author=author_of(key),
        note=THROUGH_THE_KNOBS,
        if_version=None,
    )
    return await report(slug, declared, wanted, lexicon, store, settings)


def declared_here(slug: str, key: KeyRecord, registry: RegistryDep) -> AgentConfig:
    """What the app says about this agent right now. Nothing is turned on an agent nobody holds."""
    held = registry.of(key.env, slug, held_by(key))
    if held is None or held.org != key.org:
        raise HTTPException(404, NO_AGENT.format(slug=slug))
    return held.config


async def standing(kept: TuningStore, key: KeyRecord, slug: str) -> tuple[Tuning, Lexicon]:
    """What this key's corner reads right now: its own newest, else the org's own, else nothing."""
    row = await kept.newest(key.org, key.env, held_by(key), slug)
    words = await kept.newest_lexicon(key.org, key.env, held_by(key))
    return (
        Tuning() if row is None else row.value,
        Lexicon() if words is None else words.value,
    )
