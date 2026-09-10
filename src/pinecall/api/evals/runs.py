"""POST /v1/evals/run: a suite of goldens driven through the connected app, scored and kept."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from pinecall.api._deps import (
    KeyDep,
    LlmsDep,
    LogsDep,
    LookupsDep,
    OverridesDep,
    RunsDep,
    SettingsDep,
    StoreDep,
    VaultDep,
)
from pinecall.api._live import LiveDep
from pinecall.api.agents.registry import RegistryDep
from pinecall.api.evals.runner import (
    AlreadyRunning,
    NobodyServing,
    Process,
    RunnerDep,
    Wanted,
    a_run,
)
from pinecall.evals.runs import DEFAULT_LIMIT
from pinecall.log.store import Store
from pinecall.providers.models import NoProvider
from pinecall.types import DeclarationRefused

router = APIRouter()

NO_SUCH_RUN = "no eval run {id} on this gateway"

HOW_MANY = Query(DEFAULT_LIMIT, ge=1, le=200, description="how many runs, newest first")
SINCE = Query(0.0, ge=0, description="only runs started after this many seconds since the epoch")
OF_AGENT = Query(None, description="only this agent's runs; every agent of the org's when absent")


# The key is a gate here and not an identity, exactly as the replay door beside it: it says this
# door may be opened, and nothing about the run depends on whose key it was.
@router.post("/v1/evals/run")
async def run_the_goldens(
    said: Wanted,
    key: KeyDep,  # noqa: ARG001
    runner: RunnerDep,
    registry: RegistryDep,
    overrides: OverridesDep,
    llms: LlmsDep,
    logs: LogsDep,
    live: LiveDep,
    store: StoreDep,
    runs: RunsDep,
    vault: VaultDep,
    lookups: LookupsDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    """Every golden against the app that is holding the agent, scored, stored, and answered."""
    process = Process(
        registry=registry,
        overrides=overrides,
        llms=llms,
        logs=logs,
        live=live,
        store=store,
        runs=runs,
        vault=vault,
        lookups=lookups,
        budgets=settings.budgets,
        settings=settings,
    )
    try:
        return (await a_run(said, runner, process)).as_json
    # One asker per agent, and the refusal names the run holding that agent so it can be polled.
    except AlreadyRunning as running:
        raise HTTPException(409, str(running)) from running
    except NobodyServing as nobody:
        raise HTTPException(404, str(nobody)) from nobody
    # An event the agent never declared, and a model this process has no key for: both are the
    # request asking for something this box cannot do, and both name what to change.
    except (DeclarationRefused, NoProvider) as refused:
        raise HTTPException(400, str(refused)) from refused


# A run is its agent's, and an agent is one org's: the list is cut to the key's org by asking the
# log whose each agent is, so two organisations never share one page. `agent` narrows it further,
# and a screen reading a page of the org's newest runs would lose this agent's older ones behind
# everybody else's. The agents repo's docs/decisions/evals-screen.md.
@router.get("/v1/evals/runs")
async def listed(
    key: KeyDep,
    runs: RunsDep,
    store: StoreDep,
    limit: int = HOW_MANY,
    since: float = SINCE,
    agent: str | None = OF_AGENT,
) -> dict[str, Any]:
    """The runs this gateway has done, newest first: the list a drift check diffs across."""
    newest = await runs.newest(limit, since, agent)
    return {
        "runs": [run.as_json for run in newest if await _is_the_orgs(key.org, store, run.agent)]
    }


@router.get("/v1/evals/runs/{id}")
async def one_run(id: str, key: KeyDep, runs: RunsDep, store: StoreDep) -> dict[str, Any]:
    """One run, whole: the calls it opened, and the matrix as far as it has been judged."""
    run = await runs.of(id)
    if run is None or not await _is_the_orgs(key.org, store, run.agent):
        raise HTTPException(404, NO_SUCH_RUN.format(id=id))
    return run.as_json


async def _is_the_orgs(org: str, store: Store, agent: str) -> bool:
    """Whether this agent is the org's: the log's owner, or nobody's yet."""
    owner = await store.owner(None, agent)
    return owner is None or owner == org
