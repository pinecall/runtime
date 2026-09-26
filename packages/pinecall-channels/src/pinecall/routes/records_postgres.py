"""Routes in Postgres: every number an org answers at, and the agent it rings."""

from __future__ import annotations

from typing import Any, cast

from pinecall.db import Pool
from pinecall.routes.records import Moved, door_of, the_first_of
from pinecall.types import Channel, Env, Route, parse_env

# The row is the door. One number per org, whatever channel carries it and whichever world it
# answers in, so moving a number — to another agent, or to the other world — is an update of one
# row, which is what makes `routes add` a change with no deploy behind it.
OF_ORG = """
SELECT org, number, agent, channel, env, managed
  FROM routes
 WHERE org = $1 AND env = $2
 ORDER BY added_at, number
"""

PUT = """
INSERT INTO routes (org, number, agent, channel, env, managed) VALUES ($1, $2, $3, $4, $5, $6)
     ON CONFLICT (org, number)
     DO UPDATE SET agent = excluded.agent, channel = excluded.channel, env = excluded.env,
                   managed = excluded.managed
"""

# How many numbers the box bought for the org: the stock the `numbers` quota caps, a count of
# rows and never a counter.
MANAGED = "SELECT count(*) FROM routes WHERE org = $1 AND managed"

# The door, read from the other side: an inbound message knows the number it arrived at and
# nothing about whose it is. (org, number) is the primary key, so a number that two orgs typed is
# two rows — the oldest answers, and answering.py's own warning names both.
AT = """
SELECT org, number, agent, channel, env, managed
  FROM routes
 WHERE channel = $1 AND number = $2
 ORDER BY added_at
"""

# One number of one org, in whichever world it is in. The one read that asks for no world: moving
# a number between them is the point of it, and a key opens one world while the org owns both.
OF_NUMBER = """
SELECT org, number, agent, channel, env, managed
  FROM routes
 WHERE org = $1 AND number = $2
"""

# An agent that changes org takes its doors with it. Without this the number kept answering for
# the org that no longer holds the slug, which is a number that reaches nobody: `orgs move` moved
# the log and left the route behind. A number the destination ALREADY answers at is left where it
# is and named — two orgs typing one number is a thing the schema allows and nobody should resolve
# silently (see TWO_ORGS below).
OF_AGENT = "SELECT number FROM routes WHERE agent = $1 AND org <> $2"

MOVE_TO_ORG = """
UPDATE routes SET org = $2
 WHERE agent = $1 AND org <> $2
   AND NOT EXISTS (SELECT 1 FROM routes taken WHERE taken.org = $2 AND taken.number = routes.number)
RETURNING number
"""

REMOVE = "DELETE FROM routes WHERE org = $1 AND number = $2 RETURNING number"


class PostgresRoutes:
    """The table in Postgres, read on every request: a route added now answers the next call."""

    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def of_org(self, org: str, env: Env) -> tuple[Route, ...]:
        """One indexed read on the primary key's own prefix. No cache: a cache is a stale route."""
        rows = await self._pool.fetch(OF_ORG, org, env)
        return tuple(route_of_row(row) for row in rows)

    async def at(self, channel: Channel, number: str) -> Route | None:
        """One indexed read on the door. No cache: a route added a minute ago answers this call."""
        rows = await self._pool.fetch(AT, channel, number)
        return the_first_of([route_of_row(row) for row in rows])

    async def of_number(self, org: str, number: str) -> Route | None:
        """One indexed read on the primary key, whole. The world is a column and not asked for."""
        rows = await self._pool.fetch(OF_NUMBER, org, number)
        return None if not rows else route_of_row(rows[0])

    async def put(self, route: Route) -> None:
        """Insert, or move the number: the conflict target is the door, so nothing is duplicated."""
        org, number = door_of(route)
        await self._pool.execute(
            PUT, org, number, route.agent, route.channel, route.env, route.managed
        )

    async def remove(self, org: str, number: str) -> bool:
        """The row RETURNING says whether one went, so a number nobody typed is told apart."""
        return await self._pool.fetchrow(REMOVE, org, number) is not None

    async def moved(self, agent: str, org: str) -> Moved:
        """One UPDATE, and the rows it could not take are the difference against what was there."""
        had = {str(row["number"]) for row in await self._pool.fetch(OF_AGENT, agent, org)}
        went = tuple(str(row["number"]) for row in await self._pool.fetch(MOVE_TO_ORG, agent, org))
        return Moved(went, tuple(sorted(had - set(went))))

    async def managed_by(self, org: str) -> int:
        """One count over the rows the box bought."""
        row = await self._pool.fetchrow(MANAGED, org)
        return 0 if row is None else int(row["count"])


def route_of_row(row: Any) -> Route:
    """One row back into the domain's own Route. The columns are its fields, name for name."""
    return Route(
        org=str(row["org"]),
        agent=str(row["agent"]),
        channel=cast(Channel, str(row["channel"])),
        number=str(row["number"]),
        env=parse_env(str(row["env"])),
        managed=bool(row["managed"]),
    )
