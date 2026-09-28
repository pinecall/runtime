"""An eval run: one agent, every golden under every model, judged into a matrix as it goes."""

import time
from collections.abc import AsyncGenerator, Iterable, Sequence
from contextlib import asynccontextmanager
from typing import Never
from uuid import uuid4

from livekit.agents import llm
from livekit.agents.evals import EvaluationResult
from psycopg.types.json import Jsonb

from pinecall.domain.errors import Conflict
from pinecall.domain.names import JsonObject
from pinecall.domain.scope import Scope
from pinecall.evals.case import Case, as_chat
from pinecall.evals.judges import CaseJudge
from pinecall.postgres.pool import Pool
from pinecall.wire.parts import ModelConfig
from pinecall.wire.rest.evals import (
    EvalRunResponse,
    Failure,
    JudgeScore,
    OpenedCall,
    ScoreMatrix,
    ScoreRow,
)

# A run is coroutines of the gateway, so its deadline is a timeout; its finished calls stay.
A_RUN_MAY_TAKE_S = 15 * 60


A_RUN = "run_"


# The column of the agent's own model, when the run named none.
DECLARED = "declared"


ALREADY_RUNNING = (
    "eval run {id} is running on agent {agent}: a run drives the app that also answers that "
    "agent's real calls, so there is one per agent at a time"
)


TOOK_TOO_LONG = "the run passed its {minutes} minute deadline and was stopped"


# Written whole at every change, so a run is followed while it goes.
PUT = """
INSERT INTO eval_runs (id, org, env, agent, started_at, finished_at, status, document)
VALUES (%(id)s, %(org)s, %(env)s, %(agent)s, %(started_at)s, %(finished_at)s, %(status)s,
        %(document)s)
ON CONFLICT (id) DO UPDATE SET
    finished_at = excluded.finished_at, status = excluded.status, document = excluded.document
"""


OF = "SELECT document FROM eval_runs WHERE id = %(id)s AND org = %(org)s AND env = %(env)s"


# The agent is filtered in SQL, so one agent's runs are never paged out by another's.
LISTED = """
SELECT document FROM eval_runs
WHERE org = %(org)s AND env = %(env)s AND started_at > %(since)s
  AND (%(agent)s::text IS NULL OR agent = %(agent)s)
ORDER BY started_at DESC
LIMIT %(limit)s
"""


# Not durable on purpose: a run is coroutines of this process, gone with it.
class Runner:
    """The run each agent is under in this process, if any."""

    def __init__(self) -> None:
        """No agent under a run."""
        self.running: dict[str, str] = {}

    # Refused, not queued: the refusal names the run to follow instead.
    @asynccontextmanager
    async def alone(self, run: str, agent: str) -> AsyncGenerator[None]:
        """Hold the agent for this run; Conflict while another run holds it."""
        holding = self.running.get(agent)
        if holding is not None:
            raise Conflict(ALREADY_RUNNING.format(id=holding, agent=agent))
        self.running[agent] = run
        try:
            yield
        finally:
            del self.running[agent]


def new_run(agent: str) -> EvalRunResponse:
    """A run of the agent that starts now and opened nothing yet."""
    return EvalRunResponse(
        id=f"{A_RUN}{uuid4().hex[:12]}",
        agent=agent,
        started_at=time.time(),
        finished_at=None,
        status="running",
        calls=[],
        matrix=None,
        error=None,
    )


def column_of(model: ModelConfig | None) -> str:
    """The matrix column a model's cells are under."""
    return DECLARED if model is None else f"{model.provider}/{model.model}"


def opening(run: EvalRunResponse, opened: OpenedCall) -> EvalRunResponse:
    """The run with one more call opened."""
    return run.model_copy(update={"calls": [*run.calls, opened]})


def judged(run: EvalRunResponse, cells: Sequence[ScoreRow]) -> EvalRunResponse:
    """The run with the matrix of the cells judged so far."""
    return run.model_copy(update={"matrix": matrix_of(cells)})


def finished(run: EvalRunResponse) -> EvalRunResponse:
    """The run done: every golden under every model was judged."""
    return run.model_copy(update={"status": "done", "finished_at": time.time()})


# A failing golden is a score; this is the run itself stopping early, its cells kept.
def stopped(run: EvalRunResponse, why: str) -> EvalRunResponse:
    """The run failed, and why."""
    return run.model_copy(update={"status": "failed", "finished_at": time.time(), "error": why})


async def put(pool: Pool, scope: Scope, run: EvalRunResponse) -> None:
    """Write the run whole, in its org and world."""
    params = {
        "id": run.id,
        "org": scope.org,
        "env": scope.env,
        "agent": run.agent,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "status": run.status,
        "document": Jsonb(run.written()),
    }
    async with pool.connection() as connection:
        await connection.execute(PUT, params)


async def of(pool: Pool, scope: Scope, run: str) -> EvalRunResponse | None:
    """One run of the org in the world; None for any other's."""
    async with pool.connection() as connection:
        found = await connection.execute(OF, {"id": run, "org": scope.org, "env": scope.env})
        row = await found.fetchone()
    return None if row is None else EvalRunResponse.model_validate(row["document"])


async def listed(
    pool: Pool, scope: Scope, *, agent: str | None, since: float, limit: int
) -> list[EvalRunResponse]:
    """The org's runs in the world started after `since`, newest first, one agent's or all."""
    params = {"org": scope.org, "env": scope.env, "agent": agent, "since": since, "limit": limit}
    async with pool.connection() as connection:
        rows = await (await connection.execute(LISTED, params)).fetchall()
    return [EvalRunResponse.model_validate(row["document"]) for row in rows]


async def score(
    judges: Sequence[CaseJudge], case: Case, model: llm.LLM[Never] | None
) -> list[JudgeScore]:
    """Every judge over one case, in order: its verdict as a score, and the model calls it made."""
    chat = as_chat(case)
    scores: list[JudgeScore] = []
    for judge in judges:
        result = await judge.evaluate(chat_ctx=chat, llm=model)
        scores.append(
            JudgeScore(
                metric=judge.name,
                score=EvaluationResult(judgments={judge.name: result}).score,
                passed=result.passed,
                reason=result.reasoning,
                criteria=result.instructions,
                judge_calls=judge.calls,
            )
        )
    return scores


# Every cell carries the call's own summary, never a number of its own.
def cell_of(
    opened: OpenedCall, case: Case, scores: list[JudgeScore], requests: Sequence[JsonObject] | None
) -> ScoreRow:
    """One golden under one model; a cell that did not hold carries the requests it was sent."""
    cell = ScoreRow(model=opened.model, golden=opened.golden, scores=scores, summary=case.summary)
    if all(cell_score.passed for cell_score in scores):
        return cell
    return cell.model_copy(update={"asked": None if requests is None else list(requests)})


def matrix_of(cells: Iterable[ScoreRow]) -> ScoreMatrix:
    """The matrix of these cells: the axes in the order they came, and every judge that broke."""
    rows = list(cells)
    return ScoreMatrix(
        models=list(dict.fromkeys(row.model for row in rows)),
        goldens=list(dict.fromkeys(row.golden for row in rows)),
        metrics=list(dict.fromkeys(score.metric for row in rows for score in row.scores)),
        judge_calls=sum(score.judge_calls for row in rows for score in row.scores),
        runs=rows,
        failures=[
            Failure(model=row.model, golden=row.golden, metric=score.metric)
            for row in rows
            for score in row.scores
            if not score.passed
        ],
    )
