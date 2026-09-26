"""POST /v1/evals/run: a suite of goldens driven through the connected app, scored and kept."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from pinecall.api.deps import (
    AdmissionDep,
    EvalsKeyDep,
    LiveDep,
    LlmsDep,
    LogsDep,
    LookupsDep,
    RegistryDep,
    RunsDep,
    SettingsDep,
    StoreDep,
    TuningDep,
    VaultDep,
)
from pinecall.api.evals.golden_judges import ScoreMatrix
from pinecall.api.evals.runner import (
    Process,
    RunnerDep,
    Wanted,
    run_evals,
)
from pinecall.auth.keys import is_held_by
from pinecall.evals.run_store import DEFAULT_LIMIT, EvalRun, Runs, Status
from pinecall.log.store import Store
from pinecall.providers.models import NoProvider
from pinecall.types import DeclarationRefused
from pinecall_protocol import WireModel

router = APIRouter()

NO_SUCH_RUN = "no eval run {id} on this gateway"


class OpenedCall(WireModel):
    """One golden under one model: the call it opened, so its log is readable on its own."""

    golden: str
    model: str
    call: str


class RunSaid(WireModel):
    """One run as the doors answer it: when it started, the calls it opened, the matrix so far."""

    id: str
    agent: str
    started_at: float
    finished_at: float | None
    status: Status
    calls: list[OpenedCall]
    # As far as it has been judged: a cell lands the moment its conversation ends, so a run read
    # half-way carries every verdict settled so far, whole. None until the first one settles.
    matrix: ScoreMatrix | None
    # Why the run stopped, when it did not finish. Empty on a run whose goldens simply failed.
    error: str | None


class RunList(WireModel):
    """GET /v1/evals/runs: the org's runs, newest first."""

    runs: list[RunSaid]


# The run keeps its matrix as the document scoring.py wrote (evals/run_store.py), and it is read
# back into the shape here: the same keys either way, and the door's answer says which they are.
def wire_run(run: EvalRun) -> RunSaid:
    """The run as the door answers it: one document, no nesting past the matrix."""
    return RunSaid(
        id=run.id,
        agent=run.agent,
        started_at=run.started_at,
        finished_at=run.finished_at,
        status=run.status,
        calls=[OpenedCall(golden=one.golden, model=one.model, call=one.call) for one in run.calls],
        matrix=None if run.matrix is None else ScoreMatrix.model_validate(run.matrix),
        error=run.error,
    )


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
) -> RunSaid:
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
        holder=is_held_by(key),
        vault=vault,
        admission=admission,
        lookups=lookups,
        budgets=settings.budgets,
        settings=settings,
    )
    try:
        return wire_run(await run_evals(said, runner, process))
    # One asker per agent, and the refusal names the run holding that agent so it can be polled.
    # An event the agent never declared, and a model this process has no key for: both are the
    # request asking for something this box cannot do, and both name what to change.
    except (DeclarationRefused, NoProvider) as refused:
        raise HTTPException(400, str(refused)) from refused


# A run is its agent's, and an agent is one org's: the list is cut to the key's org by asking the
# log whose each agent is, so two organisations never share one page. `agent` narrows it further,
# and a screen reading a page of the org's newest runs would lose this agent's older ones behind
# everybody else's. The agents repo's docs/decisions/evals-screen.md.
@router.get("/v1/evals/runs")
async def list_runs(
    key: EvalsKeyDep,
    runs: RunsDep,
    store: StoreDep,
    limit: int = HOW_MANY,
    since: float = SINCE,
    agent: str | None = OF_AGENT,
) -> RunList:
    """The runs this gateway has done, newest first: the list a drift check diffs across."""
    mine = await _the_orgs(key.org, runs, store, limit, since, agent)
    return RunList(runs=[wire_run(run) for run in mine])


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
async def one_run(id: str, key: EvalsKeyDep, runs: RunsDep, store: StoreDep) -> RunSaid:
    """One run, whole: the calls it opened, and the matrix as far as it has been judged."""
    run = await runs.of(id)
    if run is None or not await _is_the_orgs(key.org, store, run.agent):
        raise HTTPException(404, NO_SUCH_RUN.format(id=id))
    return wire_run(run)


async def _is_the_orgs(org: str, store: Store, agent: str) -> bool:
    """Whether this agent is the org's: the log's owner, or nobody's yet."""
    owner = await store.owner(None, agent)
    return owner is None or owner == org
