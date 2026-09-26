"""Eval runs in this process's memory, newest first, for as long as it runs."""

from __future__ import annotations

from pinecall.evals.run_store import DEFAULT_LIMIT, EvalRun


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
