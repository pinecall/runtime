"""What each org may dial, kept per org, and the ledger of every dial it asked for."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from pinecall.db import Pool
from pinecall.types import DialPolicy


@dataclass(frozen=True)
class Dial:
    """One dial this box was asked for: where it was aimed, who asked, and what became of it."""

    org: str
    env: str
    agent: str
    dialled: str
    asked_by: str
    call: str | None = None
    shown: str | None = None
    # The guard that said no, in one word, or None for a dial that was placed.
    refused: str | None = None


class DialPolicies(Protocol):
    """Where an org's outbound guards are kept. An org nobody set has the code's own defaults."""

    async def of(self, org: str) -> DialPolicy:
        """What this org may dial. The defaults, for an org nobody has set one for."""
        ...

    async def put(self, org: str, policy: DialPolicy) -> None:
        """Replace the org's guards, whole: a field left out is the default and not 'no limit'."""
        ...


class Dials(Protocol):
    """Every dial asked for, taken or refused: what the rate guard counts and an operator reads."""

    async def asked(self, dial: Dial) -> None:
        """Write one down. A refused dial is written too — a burst of them is the attack."""
        ...

    async def since(self, org: str, seconds: float) -> int:
        """How many this org has asked for in the last so-many seconds, refusals included."""
        ...


class MemoryDialling:
    """The two tables of a process with no Postgres: a dev clone's, forgotten on exit."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._policies: dict[str, DialPolicy] = {}
        self._dials: list[tuple[str, float]] = []
        self.written: list[Dial] = []

    async def of(self, org: str) -> DialPolicy:
        return self._policies.get(org, DialPolicy())

    async def put(self, org: str, policy: DialPolicy) -> None:
        self._policies[org] = policy

    async def asked(self, dial: Dial) -> None:
        self.written.append(dial)
        self._dials.append((dial.org, self._clock()))

    async def since(self, org: str, seconds: float) -> int:
        edge = self._clock() - seconds
        return sum(1 for whose, at in self._dials if whose == org and at > edge)


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


def dialling_for(pool: Pool | None) -> tuple[DialPolicies, Dials]:
    """The tables when this process opened a pool, and this process's own memory when it did not."""
    if pool is None:
        both = MemoryDialling()
        return both, both
    postgres = PostgresDialling(pool)
    return postgres, postgres


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


__all__ = ["Dial", "DialPolicies", "Dials", "MemoryDialling", "dialling_for"]
