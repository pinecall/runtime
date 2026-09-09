"""The routes an operator typed: the durable half of who answers a number, and where it is kept."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any, Protocol, cast

from pinecall.log.store import Pool
from pinecall.types import Channel, DeclarationRefused, Route

logger = logging.getLogger(__name__)

# A route an operator types answers at a number: the widget has none, and nothing dials it.
NOT_A_NUMBER = "an operator's route answers at a number, and the {channel} widget has none"

# The row is the door. One number per org, whatever channel carries it, so moving a number is
# an update of one row — which is what makes `routes add` a change with no deploy behind it.
OF_ORG = """
SELECT org, number, agent, channel
  FROM routes
 WHERE org = $1
 ORDER BY added_at, number
"""

PUT = """
INSERT INTO routes (org, number, agent, channel) VALUES ($1, $2, $3, $4)
     ON CONFLICT (org, number)
     DO UPDATE SET agent = excluded.agent, channel = excluded.channel
"""

# The door, read from the other side: an inbound message knows the number it arrived at and
# nothing about whose it is. (org, number) is the primary key, so a number that two orgs typed is
# two rows — the oldest answers, and answering.py's own warning names both.
AT = """
SELECT org, number, agent, channel
  FROM routes
 WHERE channel = $1 AND number = $2
 ORDER BY added_at
"""

REMOVE = "DELETE FROM routes WHERE org = $1 AND number = $2"

# What asyncpg answers a DELETE with when the WHERE matched nothing: the command tag, verbatim.
DELETED_NOTHING = "DELETE 0"


class Routes(Protocol):
    """Where the gateway asks which numbers an operator has assigned, and writes what it is told."""

    async def of_org(self, org: str) -> tuple[Route, ...]:
        """Every route this org's operator has typed, in the order they were typed."""
        ...

    async def at(self, channel: Channel, number: str) -> Route | None:
        """Which agent an inbound call at this door reaches, in whichever org typed it first."""
        ...

    async def put(self, route: Route) -> None:
        """Add the route, or move its number to this agent. One row per number per org."""
        ...

    async def remove(self, org: str, number: str) -> bool:
        """Forget the number. False when no row answered to it, so a typo is never silence."""
        ...


class MemoryRoutes:
    """The table of a process with no database: a dev clone routes, and forgets when it exits."""

    def __init__(self, routes: Sequence[Route] = ()) -> None:
        self._rows: dict[tuple[str, str], Route] = {door_of(route): route for route in routes}

    async def of_org(self, org: str) -> tuple[Route, ...]:
        """In the order they were typed: a re-put leaves a number where the operator saw it."""
        return tuple(route for route in self._rows.values() if route.org == org)

    async def at(self, channel: Channel, number: str) -> Route | None:
        """The first row typed for this door, and a warning when a second org typed it too."""
        return _the_first_of(
            [route for route in self._rows.values() if route.door == (channel, number)]
        )

    async def put(self, route: Route) -> None:
        """One row per number, replaced whole: the agent and the channel are both the new ones."""
        self._rows[door_of(route)] = route

    async def remove(self, org: str, number: str) -> bool:
        """Whether there was a row to forget."""
        return self._rows.pop((org, number), None) is not None


class PostgresRoutes:
    """The table in Postgres, read on every request: a route added now answers the next call."""

    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def of_org(self, org: str) -> tuple[Route, ...]:
        """One indexed read on the primary key's own prefix. No cache: a cache is a stale route."""
        rows = await self._pool.fetch(OF_ORG, org)
        return tuple(route_of_row(row) for row in rows)

    async def at(self, channel: Channel, number: str) -> Route | None:
        """One indexed read on the door. No cache: a route added a minute ago answers this call."""
        rows = await self._pool.fetch(AT, channel, number)
        return _the_first_of([route_of_row(row) for row in rows])

    async def put(self, route: Route) -> None:
        """Insert, or move the number: the conflict target is the door, so nothing is duplicated."""
        org, number = door_of(route)
        await self._pool.execute(PUT, org, number, route.agent, route.channel)

    async def remove(self, org: str, number: str) -> bool:
        """The command tag says whether a row went, so a number nobody typed is told apart."""
        tag = await self._pool.execute(REMOVE, org, number)
        return tag.strip() != DELETED_NOTHING


# A number belongs to one org in practice — an operator typed it because Meta or the carrier gave
# it to them — but nothing in the schema says so, and a silent pick between two orgs is a call
# answered by the wrong tenant. The oldest row answers, and the line names both so it is fixed.
TWO_ORGS = "%s answers in org %s: org %s typed the same number, and the older row answers"


def _the_first_of(rows: list[Route]) -> Route | None:
    """The row an inbound call takes, and a line naming any org that typed the same door."""
    if not rows:
        return None
    answering = rows[0]
    for other in rows[1:]:
        logger.warning(TWO_ORGS, answering.number, answering.org, other.org)
    return answering


def door_of(route: Route) -> tuple[str, str]:
    """The row's key. A route with no number is refused here rather than at the primary key."""
    if route.number is None:
        raise DeclarationRefused(NOT_A_NUMBER.format(channel=route.channel))
    return (route.org, route.number)


def route_of_row(row: Any) -> Route:
    """One row back into the domain's own Route. The columns are its fields, name for name."""
    return Route(
        org=str(row["org"]),
        agent=str(row["agent"]),
        channel=cast(Channel, str(row["channel"])),
        number=str(row["number"]),
    )


def routes_for(pool: Pool | None) -> Routes:
    """Postgres when the process opened one; memory when it is a clone running on a dev key."""
    return MemoryRoutes() if pool is None else PostgresRoutes(pool)
