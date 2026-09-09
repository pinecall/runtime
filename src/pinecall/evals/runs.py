"""Where an eval run is kept: the row a person reads back, in memory or in Postgres."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any, Literal, Protocol

from pinecall.log.store import Pool

# Where a run is: still opening calls, finished with a matrix, or stopped by something that broke.
# `failed` is the run failing, never a golden failing — a golden that did not hold is a score.
type Status = Literal["running", "done", "failed"]

# One row per run, replaced whole every time it moves: a run is written when it starts so that its
# calls can be tailed while it happens, and rewritten as each one opens and again as each one is
# judged. The document is the row.
PUT = """
INSERT INTO eval_runs (id, agent, started_at, finished_at, status, document)
     VALUES ($1, $2, $3::double precision, $4::double precision, $5, $6::jsonb)
     ON CONFLICT (id)
     DO UPDATE SET finished_at = excluded.finished_at,
                   status      = excluded.status,
                   document    = excluded.document
"""

OF_ID = "SELECT document FROM eval_runs WHERE id = $1"

# Newest first, which is the order a person asks in: what did the last run say, and the one before
# it. `since` is how a drift check reads only what has happened after the run it is comparing to,
# and `agent` is how one agent's screen is never handed another organisation's runs: with a busy
# org an older run of this agent would otherwise fall off a page filled by everybody else's.
NEWEST = """
SELECT document FROM eval_runs
 WHERE started_at > $1::double precision
   AND ($3::text IS NULL OR agent = $3)
 ORDER BY started_at DESC
 LIMIT $2
"""

DEFAULT_LIMIT = 20


@dataclass(frozen=True)
class Opened:
    """One golden under one model: the call it opened, so its log is readable on its own."""

    golden: str
    model: str
    call: str


@dataclass(frozen=True)
class EvalRun:
    """One run of a suite: when it started, which calls it opened, and what the graphs answered."""

    id: str
    agent: str
    started_at: float
    status: Status = "running"
    finished_at: float | None = None
    calls: tuple[Opened, ...] = ()
    # The matrix as scoring.py wrote it, as far as it has been judged: a cell lands the moment its
    # conversation ends, so a row read half-way carries every verdict settled so far, whole.
    matrix: Mapping[str, Any] | None = None
    # Why the run stopped, when it did not finish. Empty on a run whose goldens simply failed.
    error: str | None = None

    def opening(self, call: Opened) -> EvalRun:
        """The same run with one more call on it: written back as each conversation opens."""
        return replace(self, calls=(*self.calls, call))

    @property
    def as_json(self) -> dict[str, Any]:
        """The run as the door answers it, and as the store keeps it: one document, no nesting."""
        return {
            "id": self.id,
            "agent": self.agent,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "status": self.status,
            "calls": [call.__dict__ for call in self.calls],
            "matrix": self.matrix,
            "error": self.error,
        }


def a_run_of(document: Mapping[str, Any]) -> EvalRun:
    """One stored document back into the run it is. The keys are the fields, name for name."""
    return EvalRun(
        id=str(document["id"]),
        agent=str(document["agent"]),
        started_at=float(document["started_at"]),
        status=document["status"],
        finished_at=document["finished_at"],
        calls=tuple(Opened(**call) for call in document.get("calls", ())),
        matrix=document.get("matrix"),
        error=document.get("error"),
    )


class Runs(Protocol):
    """Where the gateway writes an eval run and reads the ones before it."""

    async def put(self, run: EvalRun) -> None:
        """Write the run, whole. The same call starts it, moves it and finishes it."""
        ...

    async def of(self, id: str) -> EvalRun | None:
        """One run by id, or None when nothing was ever written under it."""
        ...

    async def newest(
        self, limit: int = DEFAULT_LIMIT, since: float = 0.0, agent: str | None = None
    ) -> tuple[EvalRun, ...]:
        """The runs started after `since`, newest first, of one agent or of every agent."""
        ...


class MemoryRuns:
    """The runs of a process with no database: a dev clone evaluates, and forgets when it exits."""

    def __init__(self) -> None:
        self._rows: dict[str, EvalRun] = {}

    async def put(self, run: EvalRun) -> None:
        """One row per id, replaced whole, exactly as the upsert in Postgres replaces it."""
        self._rows[run.id] = run

    async def of(self, id: str) -> EvalRun | None:
        return self._rows.get(id)

    async def newest(
        self, limit: int = DEFAULT_LIMIT, since: float = 0.0, agent: str | None = None
    ) -> tuple[EvalRun, ...]:
        started = sorted(self._rows.values(), key=lambda run: run.started_at, reverse=True)
        wanted = (run for run in started if run.started_at > since)
        return tuple(run for run in wanted if agent is None or run.agent == agent)[:limit]


# The document is serialised here and nowhere else. `open_pool` is the plain pool the gateway's
# other tables share, and only the LOG's own store teaches its connections the jsonb codec
# (log/store/postgres.py) — so on this pool jsonb is text going out and text coming back.
class PostgresRuns:
    """The table in Postgres: a run survives the restart that happened in the middle of it."""

    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def put(self, run: EvalRun) -> None:
        """The whole document every time: three writes a run, and no partial row to reconcile."""
        await self._pool.execute(
            PUT,
            run.id,
            run.agent,
            run.started_at,
            run.finished_at,
            run.status,
            json.dumps(run.as_json),
        )

    async def of(self, id: str) -> EvalRun | None:
        row = await self._pool.fetchrow(OF_ID, id)
        return None if row is None else a_run_of(json.loads(row["document"]))

    async def newest(
        self, limit: int = DEFAULT_LIMIT, since: float = 0.0, agent: str | None = None
    ) -> tuple[EvalRun, ...]:
        rows = await self._pool.fetch(NEWEST, since, limit, agent)
        return tuple(a_run_of(json.loads(row["document"])) for row in rows)


def runs_for(pool: Pool | None) -> Runs:
    """Postgres when the process opened one; memory when it is a clone running on a dev key."""
    return MemoryRuns() if pool is None else PostgresRuns(pool)


# ── how a route asks for it ─────────────────────────────────────────────────────
