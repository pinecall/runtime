"""Dialling in Postgres: each org's policy, and every dial counted against it."""

from __future__ import annotations

from typing import Any

from pinecall.db import Pool
from pinecall.orgs.dial_policies import Dial
from pinecall.types import DialPolicy

# Replaced whole, as the quotas row is: a policy read back is what the operator last set, and a
# NULL column is the code's own default rather than an absence somebody has to remember.
_PUT = """
INSERT INTO dial_policy (org, dial_anywhere, per_minute, per_day, max_duration_s, set_at)
    VALUES ($1, $2, $3, $4, $5, now())
    ON CONFLICT (org) DO UPDATE
    SET dial_anywhere = excluded.dial_anywhere, per_minute = excluded.per_minute,
        per_day = excluded.per_day, max_duration_s = excluded.max_duration_s, set_at = now()
"""
_OF = """
SELECT dial_anywhere, per_minute, per_day, max_duration_s FROM dial_policy WHERE org = $1
"""
_ASKED = """
INSERT INTO dials (org, env, agent, call, dialled, shown, asked_by, refused)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
"""
_SINCE = """
SELECT count(*) AS asked FROM dials
WHERE org = $1 AND at > now() - make_interval(secs => $2)
"""


class PostgresDialling:
    """The two tables in Postgres: one read per dial for the policy, one for each window."""

    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def of(self, org: str) -> DialPolicy:
        """The row, or the defaults when the org was never set one."""
        row = await self._pool.fetchrow(_OF, org)
        return DialPolicy() if row is None else _a_policy(row)

    async def put(self, org: str, policy: DialPolicy) -> None:
        """One row per org, replaced whole."""
        await self._pool.execute(
            _PUT,
            org,
            policy.dial_anywhere,
            policy.per_minute,
            policy.per_day,
            policy.max_duration_s,
        )

    async def asked(self, dial: Dial) -> None:
        """One INSERT, before the call is placed: a dial nobody counted is a fence with a hole."""
        await self._pool.execute(
            _ASKED,
            dial.org,
            dial.env,
            dial.agent,
            dial.call,
            dial.dialled,
            dial.shown,
            dial.asked_by,
            dial.refused,
        )

    async def since(self, org: str, seconds: float) -> int:
        """One indexed count over (org, at): the guard asks this twice per dial."""
        row = await self._pool.fetchrow(_SINCE, org, seconds)
        return 0 if row is None else int(row["asked"])


def _a_policy(row: Any) -> DialPolicy:
    """One row back into the domain's own DialPolicy; a NULL column is the code's default."""
    standing = DialPolicy()
    return DialPolicy(
        dial_anywhere=bool(row["dial_anywhere"]),
        per_minute=standing.per_minute if row["per_minute"] is None else int(row["per_minute"]),
        per_day=standing.per_day if row["per_day"] is None else int(row["per_day"]),
        max_duration_s=(
            standing.max_duration_s if row["max_duration_s"] is None else int(row["max_duration_s"])
        ),
    )
