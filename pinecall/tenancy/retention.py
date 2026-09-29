"""Retention: the sealed calls past their org's days (policy.py), erased by the nightly run."""

from dataclasses import dataclass
from pathlib import Path

from pinecall.domain.names import parse_env
from pinecall.domain.scope import Scope
from pinecall.postgres.pool import Pool
from pinecall.tenancy import erasure

# Sealed calls only: a call still running is the caller's, not the calendar's. Oldest first, so a
# run cut short by its limit takes up where it stopped.
DUE = """
SELECT head.call, head.org, head.env, head.holder
FROM call_log_head head JOIN org_policy policy ON policy.org = head.org
WHERE head.call IS NOT NULL AND head.sealed AND head.started_at IS NOT NULL
  AND policy.retention_days IS NOT NULL
  AND head.started_at < %(now)s - policy.retention_days * 86400
ORDER BY head.started_at
LIMIT %(limit)s
"""

# Who asked, in the erasure trail, for what the calendar erased.
RETENTION = "retention"

# One night's work at most; what is left over is the next night's.
A_RUN_ERASES = 5000


@dataclass(frozen=True)
class Due:
    """A sealed call past its org's days: whose it is, and which."""

    scope: Scope
    call: str


async def due(pool: Pool, now: float, *, limit: int = A_RUN_ERASES) -> list[Due]:
    """The sealed calls whose org keeps fewer days than they have, oldest first."""
    async with pool.connection() as connection:
        rows = await (await connection.execute(DUE, {"now": now, "limit": limit})).fetchall()
    return [
        Due(
            Scope(str(row["org"]), parse_env(str(row["env"])), str(row["holder"] or "")),
            str(row["call"]),
        )
        for row in rows
    ]


async def purge(
    pool: Pool, recordings: Path, now: float, *, limit: int = A_RUN_ERASES
) -> list[str]:
    """Erase every call past its org's days, one erasure each; the calls erased."""
    erased: list[str] = []
    for call in await due(pool, now, limit=limit):
        await erasure.call(pool, recordings, call.scope, call.call, by=RETENTION)
        erased.append(call.call)
    return erased
