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
from pinecall.evals.org_runs import is_the_orgs, runs_of_org
from pinecall.evals.run_store import DEFAULT_LIMIT, EvalRun, Status
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
    mine = await runs_of_org(key.org, runs, store, limit, since, agent)
    return RunList(runs=[wire_run(run) for run in mine])


@router.get("/v1/evals/runs/{id}")
async def one_run(id: str, key: EvalsKeyDep, runs: RunsDep, store: StoreDep) -> RunSaid:
    """One run, whole: the calls it opened, and the matrix as far as it has been judged."""
    run = await runs.of(id)
    if run is None or not await is_the_orgs(key.org, store, run.agent):
        raise HTTPException(404, NO_SUCH_RUN.format(id=id))
    return wire_run(run)
