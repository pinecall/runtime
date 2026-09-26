"""Orgs in Postgres: the orgs table, its quotas and its judging, one row per org."""

from __future__ import annotations

from typing import Any

from pinecall.db import Pool
from pinecall.orgs.records import new_org_id
from pinecall.types import Org, Quotas

# A slug taken is the one refusal: the conflict target says so and RETURNING comes back empty.
_CREATE = """
INSERT INTO orgs (id, slug, name) VALUES ($1, $2, $3)
    ON CONFLICT (slug) DO NOTHING
    RETURNING id, slug, name
"""

# By production's id; the WHERE refuses a slug another row holds, the insert and the update alike
# (an empty RETURNING), so the UNIQUE on the slug is never what answers.
_MIRRORED = """
INSERT INTO orgs (id, slug, name)
SELECT $1::text, $2::text, $3::text
 WHERE NOT EXISTS (SELECT 1 FROM orgs WHERE slug = $2 AND id <> $1)
    ON CONFLICT (id) DO UPDATE SET slug = excluded.slug, name = excluded.name
RETURNING id, slug, name
"""

_LISTED = "SELECT id, slug, name FROM orgs ORDER BY created_at, id"

_FIND = "SELECT id, slug, name FROM orgs WHERE id = $1 OR slug = $1 LIMIT 1"

# The quotas row goes with the org: the foreign key cascades, so one DELETE is the whole removal.
_REMOVE = "DELETE FROM orgs WHERE id = $1 RETURNING id"

_QUOTAS = """
SELECT minutes, messages, agents, concurrent_calls, memory_facts, knowledge_chunks, numbers, seats,
       llm_tokens, budget_eur, lends
FROM quotas WHERE org = $1
"""

# Replaced whole: a limit the operator left out is NULL, which is no limit.
_SET_QUOTAS = """
INSERT INTO quotas
    (org, minutes, messages, agents, concurrent_calls, memory_facts, knowledge_chunks, numbers,
     seats, llm_tokens, budget_eur, lends, set_at)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, now())
    ON CONFLICT (org) DO UPDATE
    SET minutes = excluded.minutes, messages = excluded.messages, agents = excluded.agents,
        concurrent_calls = excluded.concurrent_calls, memory_facts = excluded.memory_facts,
        knowledge_chunks = excluded.knowledge_chunks, numbers = excluded.numbers,
        seats = excluded.seats, llm_tokens = excluded.llm_tokens,
        budget_eur = excluded.budget_eur, lends = excluded.lends, set_at = now()
"""

# NULL is on: every org from before 0027, and every org nobody turned it off for.
_JUDGES = "SELECT judging IS NOT FALSE AS judges FROM orgs WHERE id = $1"

_SET_JUDGING = "UPDATE orgs SET judging = $2 WHERE id = $1"


class PostgresOrgs:
    """The two tables in Postgres, read on every request: a quota set now bites the next call."""

    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def create(self, slug: str, name: str) -> Org | None:
        """One INSERT; an empty RETURNING is the slug already being somebody's."""
        row = await self._pool.fetchrow(_CREATE, new_org_id(), slug, name)
        return None if row is None else _an_org(row)

    async def mirrored(self, org: Org) -> Org | None:
        """One upsert; an empty RETURNING is the slug being another org's here."""
        row = await self._pool.fetchrow(_MIRRORED, org.id, org.slug, org.name)
        return None if row is None else _an_org(row)

    async def listed(self) -> tuple[Org, ...]:
        """Oldest first, so the default org is the first line of `orgs list`."""
        return tuple(_an_org(row) for row in await self._pool.fetch(_LISTED))

    async def find(self, named: str) -> Org | None:
        """One read, by either column: an id carries its prefix and a slug may not."""
        row = await self._pool.fetchrow(_FIND, named)
        return None if row is None else _an_org(row)

    async def remove(self, id: str) -> bool:
        """The row RETURNING says whether one went, so removing a stranger is told apart."""
        return await self._pool.fetchrow(_REMOVE, id) is not None

    async def quotas_of(self, id: str) -> Quotas:
        """The row, or no limits when the org was never limited."""
        row = await self._pool.fetchrow(_QUOTAS, id)
        return Quotas() if row is None else _quotas(row)

    async def set_quotas(self, id: str, quotas: Quotas) -> None:
        """One row per org, replaced whole."""
        await self._pool.execute(
            _SET_QUOTAS,
            id,
            quotas.minutes,
            quotas.messages,
            quotas.agents,
            quotas.concurrent_calls,
            quotas.memory_facts,
            quotas.knowledge_chunks,
            quotas.numbers,
            quotas.seats,
            quotas.llm_tokens,
            quotas.budget_eur,
            None if quotas.lends is None else sorted(quotas.lends),
        )

    async def judges(self, id: str) -> bool:
        """One read; an org with no row is judged, as the default says."""
        row = await self._pool.fetchrow(_JUDGES, id)
        return True if row is None else bool(row["judges"])

    async def set_judging(self, id: str, on: bool) -> None:
        """One write on the org's own row."""
        await self._pool.execute(_SET_JUDGING, id, on)


def _an_org(row: Any) -> Org:
    """One row back into the domain's own Org. The columns are its fields, name for name."""
    return Org(id=str(row["id"]), slug=str(row["slug"]), name=str(row["name"]))


def _quotas(row: Any) -> Quotas:
    """One row back into the domain's Quotas; NULL comes back as None, which is no limit."""
    return Quotas(
        minutes=row["minutes"],
        messages=row["messages"],
        agents=row["agents"],
        concurrent_calls=row["concurrent_calls"],
        memory_facts=row["memory_facts"],
        knowledge_chunks=row["knowledge_chunks"],
        numbers=row["numbers"],
        seats=row["seats"],
        llm_tokens=row["llm_tokens"],
        budget_eur=row["budget_eur"],
        lends=None if row["lends"] is None else frozenset(row["lends"]),
    )
