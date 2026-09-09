"""The two doors of an agent's pipeline: what it runs on, and turning a knob of it."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from pinecall.api._deps import KeyDep, OverridesDep, SettingsDep, StoreDep
from pinecall.api.agents.registry import NO_AGENT, RegistryDep
from pinecall.api.pipeline_report import Report, report
from pinecall.providers.overrides import Overridden
from pinecall.types import AgentConfig, DeclarationRefused

router = APIRouter()


@router.get("/v1/agents/{slug}/pipeline")
async def pipeline(
    slug: str,
    key: KeyDep,
    registry: RegistryDep,
    overrides: OverridesDep,
    store: StoreDep,
    settings: SettingsDep,
) -> Report:
    """What this agent hears, decides and speaks with, what it measured, and what is turned."""
    return await report(
        slug, _declared(slug, key.org, registry), overrides.of(slug), store, settings
    )


# PUT and not PATCH: the body is the whole set of knobs, so leaving one out is how an operator
# gives it back to the app — there is no blank value that means "unset", and a blank is refused.
@router.put("/v1/agents/{slug}/pipeline/overrides")
async def turn(
    slug: str,
    turned: Overridden,
    key: KeyDep,
    registry: RegistryDep,
    overrides: OverridesDep,
    store: StoreDep,
    settings: SettingsDep,
) -> Report:
    """Turn the knobs. Refused whole or applied whole, and the next session is built with them."""
    declared = _declared(slug, key.org, registry)
    try:
        overrides.set(slug, turned.checked(declared))
    except DeclarationRefused as refused:
        raise HTTPException(400, str(refused)) from refused
    return await report(slug, declared, overrides.of(slug), store, settings)


def _declared(slug: str, org: str, registry: RegistryDep) -> AgentConfig:
    """What the app says about this agent right now. Nothing is turned on an agent nobody holds."""
    held = registry.of(slug)
    if held is None or held.org != org:
        raise HTTPException(404, NO_AGENT.format(slug=slug))
    return held.config
