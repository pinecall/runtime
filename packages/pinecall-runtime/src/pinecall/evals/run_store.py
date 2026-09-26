"""Where an eval run is kept: the row a person reads back, in memory or in Postgres."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any, Literal, Protocol

from pinecall.db import Pool

# Where a run is: still opening calls, finished with a matrix, or stopped by something that broke.
# `failed` is the run failing, never a golden failing — a golden that did not hold is a score.
type Status = Literal["running", "done", "failed"]

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


def runs_for(pool: Pool | None) -> Runs:
    """Postgres when the process opened one; memory when it is a clone running on a dev key."""
    # Imported here: both adapters import this module for the port, and the one place that
    # picks between them is the one place the cycle would close (auth/members.py).
    from pinecall.evals.run_store_memory import MemoryRuns
    from pinecall.evals.run_store_postgres import PostgresRuns

    return MemoryRuns() if pool is None else PostgresRuns(pool)
