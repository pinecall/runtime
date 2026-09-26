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

    # A sandbox instance's orgs are production's, mirrored when a person signs in there
    # (api/accounts/identity.py): the SAME id and slug, so `pinecall link`, a key's org and every
    # door keep their words on both instances. Written over on every sign-in, so a rename at
    # production is a rename here at the next one. A slug an org of this instance's own holds under
    # another id is not taken from it.
    async def mirrored(self, org: Org) -> Org | None:
        """The org as production says it, inserted or updated by its id. None when the slug is
        another org's here."""
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

    # The one setting a tenant turns about its own org rather than about an agent: whether its
    # calls are judged at hang-up. On unless somebody turned it off — an org nobody asked about is
    # judged, as every org was before the setting existed.
    async def judges(self, id: str) -> bool:
        """Whether this org's calls are judged at hang-up."""
        ...

    async def set_judging(self, id: str, on: bool) -> None:
        """Judge this org's calls at hang-up, or stop."""
        ...


class MemoryOrgs:
    """The tenants of a process with no database: a dev clone has the default org and forgets."""

    # `rows` are tenants that arrive with ids of their own, as the ones 0006 migrated do — what
    # a test hands in beside the key records it minted for them.
    def __init__(self, rows: Sequence[Org] = ()) -> None:
        self._rows: dict[str, Org] = {DEFAULT_ORG: Org(DEFAULT_ORG, DEFAULT_ORG, DEFAULT_ORG)}
        self._rows.update({org.id: org for org in rows})
        self._quotas: dict[str, Quotas] = {}
        self._not_judged: set[str] = set()

    async def create(self, slug: str, name: str) -> Org | None:
        """One org per slug, as the table's UNIQUE would insist."""
        if any(org.slug == slug for org in self._rows.values()):
            return None
        org = Org(id=new_org_id(), slug=slug, name=name)
        self._rows[org.id] = org
        return org

    async def mirrored(self, org: Org) -> Org | None:
        """Kept under production's id, unless another org holds the slug."""
        if any(held.slug == org.slug and held.id != org.id for held in self._rows.values()):
            return None
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

    async def judges(self, id: str) -> bool:
        """On unless it was turned off."""
        return id not in self._not_judged

    async def set_judging(self, id: str, on: bool) -> None:
        """Remembered as the orgs that said no."""
        if on:
            self._not_judged.discard(id)
        else:
            self._not_judged.add(id)


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


def new_org_id() -> str:
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
        llm_tokens=row["llm_tokens"],
        budget_eur=row["budget_eur"],
        lends=None if row["lends"] is None else frozenset(row["lends"]),
    )


def orgs_for(pool: Pool | None) -> Orgs:
    """Postgres when the process opened one; memory when it is a clone running on a dev key."""
    return MemoryOrgs() if pool is None else PostgresOrgs(pool)
