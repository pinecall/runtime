"""The orgs table and the quotas beside it: who the tenants are, and what each may consume."""

from __future__ import annotations

import secrets
from collections.abc import Sequence
from typing import Any, Protocol

from pinecall.log.store import Pool
from pinecall.types import DEFAULT_ORG, Org, Quotas

# An id is minted, never typed: a slug may be renamed one day and every row that names the org
# must not notice. The default org and the tenants 0006 migrated are the exception — their id is
# the word the old `fleet` column held, so a key issued then still names the same row.
ORG_ID_PREFIX = "org_"
ORG_ID_BYTES = 6


class Orgs(Protocol):
    """Where the operator's doors create, find and remove tenants, and set what each may use."""

    async def create(self, slug: str, name: str) -> Org | None:
        """A new org, or None when the slug is already somebody's. The id is minted here."""
        ...

    async def listed(self) -> tuple[Org, ...]:
        """Every org, oldest first."""
        ...

    async def find(self, named: str) -> Org | None:
        """The org this id or slug names, or None. A door takes either; a row keeps the id."""
        ...

    async def remove(self, id: str) -> bool:
        """Forget the org and its quotas. False when no row answered to it."""
        ...

    async def quotas_of(self, id: str) -> Quotas:
        """What the org may consume. An org nobody limited has no limits."""
        ...

    async def set_quotas(self, id: str, quotas: Quotas) -> None:
        """Replace the org's limits, whole: a limit left out is no limit."""
        ...


class MemoryOrgs:
    """The tenants of a process with no database: a dev clone has the default org and forgets."""

    # `rows` are tenants that arrive with ids of their own, as the ones 0006 migrated do — what
    # a test hands in beside the key records it minted for them.
    def __init__(self, rows: Sequence[Org] = ()) -> None:
        self._rows: dict[str, Org] = {DEFAULT_ORG: Org(DEFAULT_ORG, DEFAULT_ORG, DEFAULT_ORG)}
        self._rows.update({org.id: org for org in rows})
        self._quotas: dict[str, Quotas] = {}

    async def create(self, slug: str, name: str) -> Org | None:
        """One org per slug, as the table's UNIQUE would insist."""
        if any(org.slug == slug for org in self._rows.values()):
            return None
        org = Org(id=an_org_id(), slug=slug, name=name)
        self._rows[org.id] = org
        return org

    async def listed(self) -> tuple[Org, ...]:
        """In the order they were created, which for a dict is the order they were inserted."""
        return tuple(self._rows.values())

    async def find(self, named: str) -> Org | None:
        """By id first, then by slug: the two never collide, an id carries its prefix."""
        found = self._rows.get(named)
        if found is not None:
            return found
        return next((org for org in self._rows.values() if org.slug == named), None)

    async def remove(self, id: str) -> bool:
        """Whether there was a row to forget. Its quotas go with it."""
        self._quotas.pop(id, None)
        return self._rows.pop(id, None) is not None

    async def quotas_of(self, id: str) -> Quotas:
        """Unlimited until somebody said otherwise."""
        return self._quotas.get(id, Quotas())

    async def set_quotas(self, id: str, quotas: Quotas) -> None:
        """Replaced whole, as the row is."""
        self._quotas[id] = quotas


# A slug taken is the one refusal: the conflict target says so and RETURNING comes back empty.
_CREATE = """
INSERT INTO orgs (id, slug, name) VALUES ($1, $2, $3)
    ON CONFLICT (slug) DO NOTHING
    RETURNING id, slug, name
"""

_LISTED = "SELECT id, slug, name FROM orgs ORDER BY created_at, id"

_FIND = "SELECT id, slug, name FROM orgs WHERE id = $1 OR slug = $1 LIMIT 1"

# The quotas row goes with the org: the foreign key cascades, so one DELETE is the whole removal.
_REMOVE = "DELETE FROM orgs WHERE id = $1"

_QUOTAS = """
SELECT minutes, messages, agents, concurrent_calls, memory_facts, knowledge_chunks, numbers, seats
FROM quotas WHERE org = $1
"""

# Replaced whole: a limit the operator left out is NULL, which is no limit.
_SET_QUOTAS = """
INSERT INTO quotas
    (org, minutes, messages, agents, concurrent_calls, memory_facts, knowledge_chunks, numbers,
     seats, set_at)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, now())
    ON CONFLICT (org) DO UPDATE
    SET minutes = excluded.minutes, messages = excluded.messages, agents = excluded.agents,
        concurrent_calls = excluded.concurrent_calls, memory_facts = excluded.memory_facts,
        knowledge_chunks = excluded.knowledge_chunks, numbers = excluded.numbers,
        seats = excluded.seats, set_at = now()
"""

# What asyncpg answers a DELETE with when the WHERE matched nothing: the command tag, verbatim.
DELETED_NOTHING = "DELETE 0"


class PostgresOrgs:
    """The two tables in Postgres, read on every request: a quota set now bites the next call."""

    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def create(self, slug: str, name: str) -> Org | None:
        """One INSERT; an empty RETURNING is the slug already being somebody's."""
        row = await self._pool.fetchrow(_CREATE, an_org_id(), slug, name)
        return None if row is None else _an_org(row)

    async def listed(self) -> tuple[Org, ...]:
        """Oldest first, so the default org is the first line of `orgs list`."""
        return tuple(_an_org(row) for row in await self._pool.fetch(_LISTED))

    async def find(self, named: str) -> Org | None:
        """One read, by either column: an id carries its prefix and a slug may not."""
        row = await self._pool.fetchrow(_FIND, named)
        return None if row is None else _an_org(row)

    async def remove(self, id: str) -> bool:
        """The command tag says whether a row went, so removing a stranger is told apart."""
        tag = await self._pool.execute(_REMOVE, id)
        return tag.strip() != DELETED_NOTHING

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
        )


def an_org_id() -> str:
    """A name for the row. It is not a secret: it names the tenant in every table that has one."""
    return f"{ORG_ID_PREFIX}{secrets.token_hex(ORG_ID_BYTES)}"


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
    )


def orgs_for(pool: Pool | None) -> Orgs:
    """Postgres when the process opened one; memory when it is a clone running on a dev key."""
    return MemoryOrgs() if pool is None else PostgresOrgs(pool)
