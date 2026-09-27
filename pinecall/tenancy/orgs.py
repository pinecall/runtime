"""Orgs: their names, their limits in each world, whether their calls are judged, and the fleets."""

import secrets

from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from pinecall.domain.errors import Conflict
from pinecall.domain.settings import A_FLEET_NAME
from pinecall.domain.types import PRODUCTION, Env, Org, Quotas
from pinecall.postgres.pool import Connection, Pool

ADMISSION = "admission"
FLEETS = "fleets"

CREATE = """
INSERT INTO orgs (id, slug, name) VALUES (%(id)s, %(slug)s, %(name)s)
ON CONFLICT (slug) DO NOTHING RETURNING id
"""
LISTED = "SELECT id, slug, name FROM orgs ORDER BY created_at, id"
FIND = "SELECT id, slug, name FROM orgs WHERE id = %(named)s OR slug = %(named)s LIMIT 1"
# The quotas, keys and secrets of the org go with it by the foreign keys' cascade.
REMOVE = "DELETE FROM orgs WHERE id = %(org)s RETURNING id"
QUOTAS = """
SELECT minutes, messages, agents, concurrent_calls, memory_facts, knowledge_chunks, numbers, seats,
       llm_tokens, budget_eur, lends
FROM quotas WHERE org = %(org)s AND env = %(env)s
"""
# Replaced whole: a limit left out stops being one.
SET_QUOTAS = """
INSERT INTO quotas (org, env, minutes, messages, agents, concurrent_calls, memory_facts,
                    knowledge_chunks, numbers, seats, llm_tokens, budget_eur, lends)
VALUES (%(org)s, %(env)s, %(minutes)s, %(messages)s, %(agents)s, %(concurrent_calls)s,
        %(memory_facts)s, %(knowledge_chunks)s, %(numbers)s, %(seats)s, %(llm_tokens)s,
        %(budget_eur)s, %(lends)s)
ON CONFLICT (org, env) DO UPDATE SET
    minutes = excluded.minutes, messages = excluded.messages, agents = excluded.agents,
    concurrent_calls = excluded.concurrent_calls, memory_facts = excluded.memory_facts,
    knowledge_chunks = excluded.knowledge_chunks, numbers = excluded.numbers,
    seats = excluded.seats, llm_tokens = excluded.llm_tokens, budget_eur = excluded.budget_eur,
    lends = excluded.lends, set_at = now()
"""
# A column nobody set is on: judging is what an org turns off, never what it turns on.
JUDGED = "SELECT judging IS NOT FALSE AS judged FROM orgs WHERE id = %(org)s"
SET_JUDGING = "UPDATE orgs SET judging = %(on)s WHERE id = %(org)s"
SETTING = "SELECT value FROM box_settings WHERE name = %(name)s"
SET_SETTING = """
INSERT INTO box_settings (name, value) VALUES (%(name)s, %(value)s)
ON CONFLICT (name) DO UPDATE SET value = excluded.value, set_at = now()
"""

# An id is minted, never the slug, so a slug can be renamed without touching another row.
ID_PREFIX = "org_"
ID_BYTES = 6

SLUG_TAKEN = "{slug} is taken: pick another name for the org"


class Admission(BaseModel):
    """What a newborn org may use in each world: a person's first org, and any later one."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # A world the row does not name has no limits.
    first: dict[Env, Quotas] = Field(default_factory=dict[Env, Quotas])
    # None gives a later org what the first got; set, one trial per person.
    later: dict[Env, Quotas] | None = None


class Fleets(BaseModel):
    """The fleet of workers each world's calls are dispatched to."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    production: str = Field(default="pinecall", pattern=A_FLEET_NAME)
    sandbox: str = Field(default="pinecall-sandbox", pattern=A_FLEET_NAME)


async def create(pool: Pool, slug: str, name: str, *, already: int = 0) -> Org:
    """Make an org, with the limits the box's admission gives it in each world."""
    org = Org(id=f"{ID_PREFIX}{secrets.token_hex(ID_BYTES)}", slug=slug, name=name)
    async with pool.connection() as connection, connection.transaction():
        row = await (
            await connection.execute(CREATE, {"id": org.id, "slug": slug, "name": name})
        ).fetchone()
        if row is None:
            raise Conflict(SLUG_TAKEN.format(slug=slug))
        allowed = await _admission(connection)
        worlds = allowed.first if allowed.later is None or already == 0 else allowed.later
        for env, quotas in worlds.items():
            await _set_quotas(connection, org.id, env, quotas)
    return org


async def listed(pool: Pool) -> list[Org]:
    """Every org, oldest first."""
    async with pool.connection() as connection:
        rows = await (await connection.execute(LISTED)).fetchall()
    return [_org(row) for row in rows]


async def find(pool: Pool, named: str) -> Org | None:
    """The org by its id or its slug."""
    async with pool.connection() as connection:
        row = await (await connection.execute(FIND, {"named": named})).fetchone()
    return None if row is None else _org(row)


async def remove(pool: Pool, org: str) -> bool:
    """Forget the org and everything it holds; whether there was one."""
    async with pool.connection() as connection:
        return await (await connection.execute(REMOVE, {"org": org})).fetchone() is not None


async def quotas_of(pool: Pool, org: str, env: Env) -> Quotas:
    """The org's limits in the world; none when nobody set them."""
    async with pool.connection() as connection:
        row = await (await connection.execute(QUOTAS, {"org": org, "env": env})).fetchone()
    if row is None:
        return Quotas()
    lends = row.pop("lends")
    return Quotas(**row, lends=None if lends is None else frozenset(lends))


async def set_quotas(pool: Pool, org: str, env: Env, quotas: Quotas) -> None:
    """Replace the org's limits in the world, whole."""
    async with pool.connection() as connection:
        await _set_quotas(connection, org, env, quotas)


async def judged(pool: Pool, org: str) -> bool:
    """Whether the org's calls are judged at hang-up."""
    async with pool.connection() as connection:
        row = await (await connection.execute(JUDGED, {"org": org})).fetchone()
    return row is None or bool(row["judged"])


async def set_judging(pool: Pool, org: str, *, on: bool) -> None:
    """Turn the org's judging on or off."""
    async with pool.connection() as connection:
        await connection.execute(SET_JUDGING, {"org": org, "on": on})


async def admission(pool: Pool) -> Admission:
    """What a newborn org is given; nothing limited on a box that never said."""
    async with pool.connection() as connection:
        return await _admission(connection)


async def set_admission(pool: Pool, allowed: Admission) -> None:
    """Write what a newborn org is given, whole, as the console's box screen sends it."""
    async with pool.connection() as connection:
        await connection.execute(SET_SETTING, {"name": ADMISSION, "value": _jsonb(allowed)})


async def fleets(pool: Pool) -> Fleets:
    """The fleet each world's calls go to."""
    async with pool.connection() as connection:
        row = await (await connection.execute(SETTING, {"name": FLEETS})).fetchone()
    return Fleets() if row is None else Fleets.model_validate(row["value"])


async def set_fleets(pool: Pool, named: Fleets) -> None:
    """Write the fleet of each world, whole."""
    async with pool.connection() as connection:
        await connection.execute(SET_SETTING, {"name": FLEETS, "value": _jsonb(named)})


def fleet_of(named: Fleets, env: Env) -> str:
    """The fleet a call of the world is dispatched to."""
    return named.production if env == PRODUCTION else named.sandbox


async def _admission(connection: Connection) -> Admission:
    row = await (await connection.execute(SETTING, {"name": ADMISSION})).fetchone()
    return Admission() if row is None else Admission.model_validate(row["value"])


async def _set_quotas(connection: Connection, org: str, env: Env, quotas: Quotas) -> None:
    lends = None if quotas.lends is None else sorted(quotas.lends)
    await connection.execute(
        SET_QUOTAS,
        {"org": org, "env": env, **quotas.limits, "budget_eur": quotas.budget_eur, "lends": lends},
    )


def _org(row: DictRow) -> Org:
    return Org(id=row["id"], slug=row["slug"], name=row["name"])


def _jsonb(written: BaseModel) -> Jsonb:
    return Jsonb(written.model_dump(mode="json", exclude_defaults=False))
