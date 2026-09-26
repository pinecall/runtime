"""The door of an agent's pipeline: what it hears, decides and speaks with, and what that cost."""

from __future__ import annotations

from fastapi import APIRouter

from pinecall.api.agents.pipeline_report import Report, report
from pinecall.api.deps import PipelineKeyDep, SettingsDep, StoreDep, TuningDep
from pinecall.api.scope.request_scope import HeldDep
from pinecall.auth.keys import is_held_by
from pinecall.orgs import standing_in

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
    standing = await standing_in(kept, key.org, key.env, is_held_by(key), slug)
    return await report(slug, held.config, standing.tuning, standing.lexicon, store, settings)
