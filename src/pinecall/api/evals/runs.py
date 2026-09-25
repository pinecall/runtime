"""POST /v1/evals/run: a suite of goldens driven through the connected app, scored and kept."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from pinecall.api._deps import (
    AdmissionDep,
    EvalsKeyDep,
    LlmsDep,
    LogsDep,
    LookupsDep,
    RunsDep,
    SettingsDep,
    StoreDep,
    TuningDep,
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
from pinecall.auth.keys import held_by
from pinecall.evals.runs import DEFAULT_LIMIT, EvalRun, Runs
from pinecall.log.store import Store
from pinecall.providers.models import NoProvider
from pinecall.types import DeclarationRefused

router = APIRouter()

NO_SUCH_RUN = "no eval run {id} on this gateway"

HOW_MANY = Query(DEFAULT_LIMIT, ge=1, le=200, description="how many runs, newest first")

# How far the read-ahead below will go for one page: a box where this org ran nothing lately does
# not read its whole history to answer "the newest twenty of yours".
MOST_READ_AHEAD = 400
SINCE = Query(0.0, ge=0, description="only runs started after this many seconds since the epoch")
OF_AGENT = Query(None, description="only this agent's runs; every agent of the org's when absent")


# The key says which world's app the run is put to, and nothing else about the run depends on
# whose key it was: the replay door beside it takes the same key as a gate alone.
@router.post("/v1/evals/run")
async def run_the_goldens(
    said: Wanted,
    key: EvalsKeyDep,
    runner: RunnerDep,
    registry: RegistryDep,
    tuning: TuningDep,
    llms: LlmsDep,
    logs: LogsDep,
    live: LiveDep,
    store: StoreDep,
    runs: RunsDep,
    vault: VaultDep,
    lookups: LookupsDep,
    settings: SettingsDep,
    admission: AdmissionDep,
) -> dict[str, Any]:
    """Every golden against the app that is holding the agent, scored, stored, and answered."""
    process = Process(
        registry=registry,
        tuning=tuning,
        llms=llms,
        logs=logs,
        live=live,
        store=store,
        runs=runs,
        env=key.env,
        holder=held_by(key),
        vault=vault,
        admission=admission,
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
    key: EvalsKeyDep,
    runs: RunsDep,
    store: StoreDep,
    limit: int = HOW_MANY,
    since: float = SINCE,
    agent: str | None = OF_AGENT,
) -> dict[str, Any]:
    """The runs this gateway has done, newest first: the list a drift check diffs across."""
    return {
        "runs": [run.as_json for run in await _the_orgs(key.org, runs, store, limit, since, agent)]
    }


# The cut belongs AFTER the org filter, and used to come before it: `newest(limit)` took the box's
# newest runs whatever org they belong to, and what was left after the filter was whatever share
# of them happened to be this org's — `?limit=2` answered an empty list on a box where two other
# tenants had run last (`pinecall runs list --limit 2`, production, 2026-09-20). So this reads
# ahead, in pages, until it has `limit` of the org's or the table is exhausted.
async def _the_orgs(
    org: str, runs: Runs, store: Store, limit: int, since: float, agent: str | None
) -> list[EvalRun]:
    """The newest `limit` runs OF THIS ORG, newest first."""
    mine: list[EvalRun] = []
    read = 0
    while len(mine) < limit:
        asked = min(MOST_READ_AHEAD, max(limit * 2, limit + read))
        page = await runs.newest(asked, since, agent)
        for run in page[read:]:
            if await _is_the_orgs(org, store, run.agent):
                mine.append(run)
                if len(mine) == limit:
                    break
        if len(page) <= read or len(page) < asked:
            break
        read = len(page)
    return mine


@router.get("/v1/evals/runs/{id}")
async def one_run(id: str, key: EvalsKeyDep, runs: RunsDep, store: StoreDep) -> dict[str, Any]:
    """One run, whole: the calls it opened, and the matrix as far as it has been judged."""
    run = await runs.of(id)
    if run is None or not await _is_the_orgs(key.org, store, run.agent):
        raise HTTPException(404, NO_SUCH_RUN.format(id=id))
    return run.as_json


async def _is_the_orgs(org: str, store: Store, agent: str) -> bool:
    """Whether this agent is the org's: the log's owner, or nobody's yet."""
    owner = await store.owner(None, agent)
    return owner is None or owner == org
