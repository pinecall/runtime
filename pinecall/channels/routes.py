"""Which agent answers a number or a channel, per org and world: the routes table."""

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from psycopg.rows import DictRow

from pinecall.domain.call import Route
from pinecall.domain.names import Channel, Env, RouteOrigin
from pinecall.postgres.pool import Pool

logger = logging.getLogger(__name__)

OF_ORG = """
SELECT org, number, agent, channel, env, managed FROM routes
WHERE org = %(org)s AND env = %(env)s
ORDER BY added_at, number
"""
# The schema lets two orgs type the same number: the oldest row answers, the others are named.
AT = """
SELECT org, number, agent, channel, env, managed FROM routes
WHERE channel = %(channel)s AND number = %(number)s
ORDER BY added_at
"""
TWO_ORGS = "%s answers in org %s: org %s typed the same number, and the older row answers"
OF_NUMBER = """
SELECT org, number, agent, channel, env, managed FROM routes
WHERE org = %(org)s AND number = %(number)s
"""
# A number added again moves: its agent, channel, world, account and origin are the newest said.
PUT = """
INSERT INTO routes (org, number, agent, channel, env, managed, account, networks, origin)
VALUES (%(org)s, %(number)s, %(agent)s, %(channel)s, %(env)s, %(managed)s, %(account)s,
        %(networks)s, %(origin)s)
ON CONFLICT (org, number) DO UPDATE SET agent = excluded.agent, channel = excluded.channel,
    env = excluded.env, managed = excluded.managed, account = excluded.account,
    networks = excluded.networks, origin = excluded.origin
"""
REMOVE = "DELETE FROM routes WHERE org = %(org)s AND number = %(number)s RETURNING number"
MOVE = "UPDATE routes SET env = %(env)s WHERE org = %(org)s AND number = %(number)s RETURNING env"
MANAGED = "SELECT count(*) AS bought FROM routes WHERE org = %(org)s AND env = %(env)s AND managed"
# A number the target org already typed stays where it was: one row per number per org.
WITH_AGENT = """
UPDATE routes SET org = %(org)s
WHERE agent = %(agent)s AND org <> %(org)s
  AND NOT EXISTS (SELECT 1 FROM routes AS theirs WHERE theirs.org = %(org)s
                  AND theirs.number = routes.number)
RETURNING number
"""
STAYED = "SELECT number FROM routes WHERE agent = %(agent)s AND org <> %(org)s ORDER BY number"
# A row with what the table keeps beside it: how it was written, the kind of the account its
# number lives in, and the org whose row answers the number (the oldest, as AT reads it).
RECORDS = """
SELECT routes.org, routes.number, routes.agent, routes.channel, routes.env, routes.managed,
       routes.origin, carriers.kind AS carrier,
       (SELECT first.org FROM routes AS first
        WHERE first.channel = routes.channel AND first.number = routes.number
        ORDER BY first.added_at LIMIT 1) AS answering
FROM routes LEFT JOIN carriers
    ON carriers.org = routes.org AND carriers.account = routes.account
"""


@dataclass(frozen=True)
class RouteRecord:
    """A route with how it was written, its account's kind, and the org that answers its number."""

    route: Route
    origin: RouteOrigin
    # None for a number no account of the org holds: bought by the box, hooked, typed, or an
    # account since forgotten.
    carrier: str | None
    # The org whose row answers the number: the route's own, or an older row of another org.
    answering: str


ON_THE_BOX = RECORDS + "ORDER BY routes.number, routes.channel, routes.added_at"
OF_NUMBER_RECORD = RECORDS + "WHERE routes.org = %(org)s AND routes.number = %(number)s"
OF_ORG_RECORDS = (
    RECORDS
    + "WHERE routes.org = %(org)s AND routes.env = %(env)s ORDER BY routes.added_at, routes.number"
)


async def of_org(pool: Pool, org: str, env: Env) -> list[Route]:
    """The org's numbers in one world, oldest first."""
    async with pool.connection() as connection:
        rows = await (await connection.execute(OF_ORG, {"org": org, "env": env})).fetchall()
    return [_route(row) for row in rows]


async def at(pool: Pool, channel: Channel, number: str) -> Route | None:
    """The route a call dialled to this number takes, whatever org typed it."""
    params = {"channel": channel, "number": number}
    async with pool.connection() as connection:
        rows = await (await connection.execute(AT, params)).fetchall()
    if not rows:
        return None
    answering = _route(rows[0])
    for other in rows[1:]:
        logger.warning(TWO_ORGS, number, answering.org, other["org"])
    return answering


async def on_the_box(pool: Pool) -> list[RouteRecord]:
    """Every route of every org and world, by number, and which of them answers each number."""
    async with pool.connection() as connection:
        rows = await (await connection.execute(ON_THE_BOX)).fetchall()
    return [_record(row) for row in rows]


async def records_of(pool: Pool, org: str, env: Env) -> list[RouteRecord]:
    """The org's routes in one world, oldest first, each with what the table keeps beside it."""
    async with pool.connection() as connection:
        rows = await (await connection.execute(OF_ORG_RECORDS, {"org": org, "env": env})).fetchall()
    return [_record(row) for row in rows]


async def record_of(pool: Pool, org: str, number: str) -> RouteRecord | None:
    """The org's route at this number, whatever world it is in, with what the table keeps."""
    params = {"org": org, "number": number}
    async with pool.connection() as connection:
        row = await (await connection.execute(OF_NUMBER_RECORD, params)).fetchone()
    return None if row is None else _record(row)


async def of_number(pool: Pool, org: str, number: str) -> Route | None:
    """The org's route at this number, whatever world it is in."""
    async with pool.connection() as connection:
        row = await (await connection.execute(OF_NUMBER, {"org": org, "number": number})).fetchone()
    return None if row is None else _route(row)


async def put(
    pool: Pool,
    route: Route,
    *,
    origin: RouteOrigin,
    account: str | None,
    networks: Sequence[str] = (),
) -> None:
    """Keep the route, moving the number if the org had it: how, and the account it lives in."""
    row = {
        "org": route.org,
        "number": route.number,
        "agent": route.agent,
        "channel": route.channel,
        "env": route.env,
        "managed": route.managed,
        "account": account,
        "networks": list(networks),
        "origin": origin,
    }
    async with pool.connection() as connection:
        await connection.execute(PUT, row)


async def remove(pool: Pool, org: str, number: str) -> bool:
    """Forget the org's route at the number; whether there was one."""
    async with pool.connection() as connection:
        gone = await connection.execute(REMOVE, {"org": org, "number": number})
        return await gone.fetchone() is not None


async def moved(pool: Pool, org: str, number: str, env: Env) -> bool:
    """Move the org's number into the world; whether it had it."""
    async with pool.connection() as connection:
        done = await connection.execute(MOVE, {"org": org, "number": number, "env": env})
        return await done.fetchone() is not None


async def moved_with_agent(pool: Pool, agent: str, org: str) -> tuple[list[str], list[str]]:
    """Move the agent's numbers into the org; the numbers moved, and the ones that stayed."""
    async with pool.connection() as connection, connection.transaction():
        moved = await (
            await connection.execute(WITH_AGENT, {"agent": agent, "org": org})
        ).fetchall()
        stayed = await (await connection.execute(STAYED, {"agent": agent, "org": org})).fetchall()
    return sorted(str(row["number"]) for row in moved), [str(row["number"]) for row in stayed]


async def managed_in(pool: Pool, org: str, env: Env) -> int:
    """How many of the org's numbers in the world the box bought."""
    async with pool.connection() as connection:
        row = await (await connection.execute(MANAGED, {"org": org, "env": env})).fetchone()
    return 0 if row is None else int(row["bought"])


def _route(row: DictRow) -> Route:
    return Route(
        org=row["org"],
        agent=row["agent"],
        channel=row["channel"],
        number=row["number"],
        env=row["env"],
        managed=row["managed"],
    )


def _record(row: DictRow) -> RouteRecord:
    return RouteRecord(
        route=_route(row), origin=row["origin"], carrier=row["carrier"], answering=row["answering"]
    )
