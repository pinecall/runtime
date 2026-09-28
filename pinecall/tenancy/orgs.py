"""Orgs: their names, the quotas they are born with, and whether their calls are judged."""

import secrets

from psycopg.rows import DictRow

from pinecall.domain.errors import Conflict
from pinecall.domain.org import Org
from pinecall.postgres.pool import Pool
from pinecall.tenancy.admission import give_first_quotas

CREATE = """
INSERT INTO orgs (id, slug, name) VALUES (%(id)s, %(slug)s, %(name)s)
ON CONFLICT (slug) DO NOTHING RETURNING id
"""
LISTED = "SELECT id, slug, name FROM orgs ORDER BY created_at, id"
FIND = "SELECT id, slug, name FROM orgs WHERE id = %(named)s OR slug = %(named)s LIMIT 1"
# The quotas, keys and secrets of the org go with it by the foreign keys' cascade.
REMOVE = "DELETE FROM orgs WHERE id = %(org)s RETURNING id"
# A column nobody set is on: judging is what an org turns off, never what it turns on.
JUDGED = "SELECT judging IS NOT FALSE AS judged FROM orgs WHERE id = %(org)s"
SET_JUDGING = "UPDATE orgs SET judging = %(on)s WHERE id = %(org)s"

# An id is minted, never the slug, so a slug can be renamed without touching another row.
ID_PREFIX = "org_"
ID_BYTES = 6

SLUG_TAKEN = "{slug} is taken: pick another name for the org"


async def create(pool: Pool, slug: str, name: str, *, already: int = 0) -> Org:
    """Make an org, with the limits the box's admission gives it in each world."""
    org = Org(id=f"{ID_PREFIX}{secrets.token_hex(ID_BYTES)}", slug=slug, name=name)
    async with pool.connection() as connection, connection.transaction():
        row = await (
            await connection.execute(CREATE, {"id": org.id, "slug": slug, "name": name})
        ).fetchone()
        if row is None:
            raise Conflict(SLUG_TAKEN.format(slug=slug))
        await give_first_quotas(connection, org.id, already=already)
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


async def judged(pool: Pool, org: str) -> bool:
    """Whether the org's calls are judged at hang-up."""
    async with pool.connection() as connection:
        row = await (await connection.execute(JUDGED, {"org": org})).fetchone()
    return row is None or bool(row["judged"])


async def set_judging(pool: Pool, org: str, *, on: bool) -> None:
    """Turn the org's judging on or off."""
    async with pool.connection() as connection:
        await connection.execute(SET_JUDGING, {"org": org, "on": on})


def _org(row: DictRow) -> Org:
    return Org(id=row["id"], slug=row["slug"], name=row["name"])
