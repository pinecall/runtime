"""The door of an agent's pipeline: what it hears, decides and speaks with, and what that cost."""

from __future__ import annotations

from fastapi import APIRouter

from pinecall.api.agents.pipeline_report import Report, report
from pinecall.api.deps import PipelineKeyDep, SettingsDep, StoreDep, TuningDep
from pinecall.api.scope.request_scope import HeldDep
from pinecall.auth.keys import KeyRecord, is_held_by
from pinecall.orgs.tuning_store import TuningStore
from pinecall.types import Lexicon, Tuning

router = APIRouter()


@router.get("/v1/agents/{slug}/pipeline")
async def pipeline(
    slug: str,
    key: PipelineKeyDep,
    held: HeldDep,
    kept: TuningDep,
    store: StoreDep,
    settings: SettingsDep,
) -> Report:
    """What this agent hears, decides and speaks with, what it measured, and what is set."""
    tuning, lexicon = await current_settings(kept, key, slug)
    return await report(slug, held.config, tuning, lexicon, store, settings)


async def current_settings(kept: TuningStore, key: KeyRecord, slug: str) -> tuple[Tuning, Lexicon]:
    """What this key's corner reads right now: its own newest, else the org's own, else nothing."""
    row = await kept.newest(key.org, key.env, is_held_by(key), slug)
    words = await kept.newest_lexicon(key.org, key.env, is_held_by(key))
    return (
        Tuning() if row is None else row.value,
        Lexicon() if words is None else words.value,
    )
