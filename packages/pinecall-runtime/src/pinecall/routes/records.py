"""The routes an operator typed: the durable half of who answers a number, and where it is kept."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any, Protocol, cast

from pinecall.db import Pool
from pinecall.types import Channel, DeclarationRefused, Env, Route, parse_env

logger = logging.getLogger(__name__)

# A route an operator types answers at a number: the widget has none, and nothing dials it.
NOT_A_NUMBER = "an operator's route answers at a number, and the {channel} widget has none"

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


@dataclass(frozen=True)
class Moved:
    """What `orgs move` did to an agent's doors: the numbers that went, and any that could not."""

    numbers: tuple[str, ...] = ()
    # Left where they were because the destination org already answers at that very number. Named
    # rather than resolved: which of two rows a call takes is not this verb's to decide.
    stayed: tuple[str, ...] = ()


class Routes(Protocol):
    """Where the gateway asks which numbers an operator has assigned, and writes what it is told."""

    async def of_org(self, org: str, env: Env) -> tuple[Route, ...]:
        """Every route this org's operator typed into this world, in the order they were typed."""
        ...

    async def at(self, channel: Channel, number: str) -> Route | None:
        """Which agent an inbound call at this door reaches, in whichever org and world typed it."""
        ...

    async def of_number(self, org: str, number: str) -> Route | None:
        """This org's row for this number, in whichever world it is in. None when it has none."""
        ...

    async def put(self, route: Route) -> None:
        """Add the route, or move its number to this agent. One row per number per org."""
        ...

    async def remove(self, org: str, number: str) -> bool:
        """Forget the number. False when no row answered to it, so a typo is never silence."""
        ...

    async def moved(self, agent: str, org: str) -> Moved:
        """This agent's doors into that org: what went, and what the org already answered at."""
        ...

    async def managed_by(self, org: str) -> int:
        """How many numbers the box bought for this org: what the `numbers` quota is measured on."""
        ...


class MemoryRoutes:
    """The table of a process with no database: a dev clone routes, and forgets when it exits."""

    def __init__(self, routes: Sequence[Route] = ()) -> None:
        self._rows: dict[tuple[str, str], Route] = {door_of(route): route for route in routes}

    async def of_org(self, org: str, env: Env) -> tuple[Route, ...]:
        """In the order they were typed: a re-put leaves a number where the operator saw it."""
        return tuple(
            route for route in self._rows.values() if route.org == org and route.env == env
        )

    async def at(self, channel: Channel, number: str) -> Route | None:
        """The first row typed for this door, and a warning when a second org typed it too."""
        return _the_first_of(
            [route for route in self._rows.values() if route.door == (channel, number)]
        )

    async def of_number(self, org: str, number: str) -> Route | None:
        """The row itself: the dict is keyed by exactly this pair."""
        return self._rows.get((org, number))

    async def put(self, route: Route) -> None:
        """One row per number, replaced whole: the agent and the channel are both the new ones."""
        self._rows[door_of(route)] = route

    async def remove(self, org: str, number: str) -> bool:
        """Whether there was a row to forget."""
        return self._rows.pop((org, number), None) is not None

    async def moved(self, agent: str, org: str) -> Moved:
        """Re-keyed under the new org, one row at a time, leaving a number it already answers at."""
        taken = {route.number for route in self._rows.values() if route.org == org}
        went: list[str] = []
        stayed: list[str] = []
        for route in list(self._rows.values()):
            if route.agent != agent or route.org == org or route.number is None:
                continue
            if route.number in taken:
                stayed.append(route.number)
                continue
            del self._rows[door_of(route)]
            moved_to = replace(route, org=org)
            self._rows[door_of(moved_to)] = moved_to
            went.append(route.number)
        return Moved(tuple(went), tuple(stayed))

    async def managed_by(self, org: str) -> int:
        """A count of the rows the box bought, across both worlds."""
        return sum(1 for route in self._rows.values() if route.org == org and route.managed)


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
        return _the_first_of([route_of_row(row) for row in rows])

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
        env=parse_env(str(row["env"])),
        managed=bool(row["managed"]),
    )


def routes_for(pool: Pool | None) -> Routes:
    """Postgres when the process opened one; memory when it is a clone running on a dev key."""
    return MemoryRoutes() if pool is None else PostgresRoutes(pool)
