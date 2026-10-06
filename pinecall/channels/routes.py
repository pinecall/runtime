"""Which agent answers a number or a channel, per org and world: the routes table."""

from dataclasses import dataclass

from psycopg.rows import DictRow

from pinecall.domain.call import Route
from pinecall.domain.names import Channel, Env, RouteOrigin
from pinecall.postgres.pool import Pool, box_wide

OF_ORG = """
SELECT org, number, agent, channel, env, managed FROM routes
WHERE org = %(org)s AND env = %(env)s
ORDER BY added_at, number
"""
# One org holds a number (routes_number_held_once), so a number reads one row at most.
AT = """
SELECT org, number, agent, channel, env, managed FROM routes
WHERE channel = %(channel)s AND number = %(number)s
"""
OF_NUMBER = """
SELECT org, number, agent, channel, env, managed FROM routes
WHERE org = %(org)s AND number = %(number)s
"""
# A number added again moves: its agent, channel, world and how it came are the newest said. One
# written to wait (a hooked number, where the box asks for approval) is unapproved until the
# operator approves it; one approved once stays approved however it comes again.
PUT = """
INSERT INTO routes (org, number, agent, channel, env, managed, account, networks, origin, via,
                    approved_at)
VALUES (%(org)s, %(number)s, %(agent)s, %(channel)s, %(env)s, %(managed)s, %(account)s,
        %(networks)s, %(origin)s, %(via)s,
        CASE WHEN %(waits)s THEN NULL ELSE now() END)
ON CONFLICT (org, number) DO UPDATE SET agent = excluded.agent, channel = excluded.channel,
    env = excluded.env, managed = excluded.managed, account = excluded.account,
    networks = excluded.networks, origin = excluded.origin, via = excluded.via,
    approved_at = COALESCE(routes.approved_at, excluded.approved_at)
"""
APPROVE = """
UPDATE routes SET approved_at = now(), approved_by = %(by)s
WHERE org = %(org)s AND number = %(number)s AND approved_at IS NULL
RETURNING number
"""
WAITING = """
SELECT 1 FROM routes WHERE org = %(org)s AND number = %(number)s AND approved_at IS NULL
"""
ELSEWHERE = "SELECT 1 FROM routes WHERE number = %(number)s AND org <> %(org)s"
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
# A number rung all day is written once a minute, never once a call.
CALLED = """
UPDATE routes SET last_call_at = now()
WHERE org = %(org)s AND number = %(number)s
  AND (last_call_at IS NULL OR last_call_at < now() - interval '1 minute')
"""
# A row with what the table keeps beside it: how it was written, the kind of the account its
# number lives in, and whether the operator approved it.
RECORDS = """
SELECT routes.org, routes.number, routes.agent, routes.channel, routes.env, routes.managed,
       routes.origin, routes.via, routes.last_call_at, routes.account, routes.networks,
       routes.approved_at IS NOT NULL AS approved,
       carriers.kind AS carrier
FROM routes LEFT JOIN carriers
    ON carriers.org = routes.org AND carriers.account = routes.account
"""


@dataclass(frozen=True)
class RouteWrite:
    """What a row keeps beside its route: who wrote it, the account, the networks, the carrier."""

    origin: RouteOrigin
    account: str | None = None
    # A number hooked from networks of the org's own, which the operator approves.
    networks: tuple[str, ...] = ()
    # The catalog carrier a number with no account comes through.
    via: str | None = None
    # Written unapproved: no call to it opens until the operator approves it.
    waits: bool = False


@dataclass(frozen=True)
class RouteRecord:
    """A route with how it was written, its account's kind, and whether it was approved."""

    route: Route
    origin: RouteOrigin
    # None for a number no account of the org holds: bought by the box, hooked, typed, or an
    # account since forgotten.
    carrier: str | None
    via: str | None = None
    # When a call to the number last reached the box; None when none ever did.
    last_call_at: float | None = None
    # The org's account the number lives in, and a hooked number's own networks.
    account: str | None = None
    networks: tuple[str, ...] = ()
    # False for a number the org hooked that the operator has not approved: no call to it opens.
    approved: bool = True


ON_THE_BOX = RECORDS + "ORDER BY routes.number, routes.channel, routes.added_at"
OF_NUMBER_RECORD = RECORDS + "WHERE routes.org = %(org)s AND routes.number = %(number)s"
PHONES_OF_ORG = (
    RECORDS
    + "WHERE routes.org = %(org)s AND routes.channel = 'phone' AND routes.number IS NOT NULL "
    + "ORDER BY routes.added_at, routes.number"
)
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
    """The route a call dialled to this number takes, whatever org holds it."""
    params = {"channel": channel, "number": number}
    with box_wide():
        async with pool.connection() as connection:
            row = await (await connection.execute(AT, params)).fetchone()
    return None if row is None else _route(row)


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


async def phones_of(pool: Pool, org: str) -> list[RouteRecord]:
    """The org's phone numbers in both worlds, oldest first, with what the table keeps."""
    async with pool.connection() as connection:
        rows = await (await connection.execute(PHONES_OF_ORG, {"org": org})).fetchall()
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


async def put(pool: Pool, route: Route, written: RouteWrite) -> None:
    """Keep the route, moving the number if the org had it, with how it was written."""
    row = {
        "org": route.org,
        "number": route.number,
        "agent": route.agent,
        "channel": route.channel,
        "env": route.env,
        "managed": route.managed,
        "account": written.account,
        "networks": list(written.networks),
        "origin": written.origin,
        "via": written.via,
        "waits": written.waits,
    }
    async with pool.connection() as connection:
        await connection.execute(PUT, row)


async def called(pool: Pool, org: str, number: str) -> None:
    """Note that a call to the org's number reached the box now."""
    async with pool.connection() as connection:
        await connection.execute(CALLED, {"org": org, "number": number})


async def approve(pool: Pool, org: str, number: str, by: str) -> bool:
    """The operator's word that the org's hooked number is the org's; whether one waited."""
    async with pool.connection() as connection:
        done = await connection.execute(APPROVE, {"org": org, "number": number, "by": by})
        return await done.fetchone() is not None


async def is_waiting(pool: Pool, org: str, number: str) -> bool:
    """Whether the org's number is one it hooked that the operator has not approved."""
    async with pool.connection() as connection:
        row = await (await connection.execute(WAITING, {"org": org, "number": number})).fetchone()
    return row is not None


async def held_elsewhere(pool: Pool, org: str, number: str) -> bool:
    """Whether another org holds the number on this box."""
    with box_wide():
        async with pool.connection() as connection:
            found = await connection.execute(ELSEWHERE, {"org": org, "number": number})
            return await found.fetchone() is not None


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
        route=_route(row),
        origin=row["origin"],
        carrier=row["carrier"],
        via=row["via"],
        last_call_at=None if row["last_call_at"] is None else row["last_call_at"].timestamp(),
        account=row["account"],
        networks=tuple(str(network) for network in row["networks"] or ()),
        approved=bool(row["approved"]),
    )
