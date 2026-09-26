"""Eval runs in Postgres: one row a run, its document whole as jsonb."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from pinecall.db import Pool
from pinecall.evals.run_store import DEFAULT_LIMIT, EvalRun, Opened

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


def run_from_document(document: Mapping[str, Any]) -> EvalRun:
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


# The document is serialised here and nowhere else. `open_pool` is the plain pool the gateway's
# other tables share, and only the LOG's own store teaches its connections the jsonb codec
# (db/connecting.py, jsonb_as_dicts) — so on this pool jsonb is text going out and text coming back.
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
        return None if row is None else run_from_document(json.loads(row["document"]))

    async def newest(
        self, limit: int = DEFAULT_LIMIT, since: float = 0.0, agent: str | None = None
    ) -> tuple[EvalRun, ...]:
        rows = await self._pool.fetch(NEWEST, since, limit, agent)
        return tuple(run_from_document(json.loads(row["document"])) for row in rows)
