"""The routes an operator typed: the durable half of who answers a number, and where it is kept."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from pinecall.db import Pool
from pinecall.types import Channel, DeclarationRefused, Env, Route

logger = logging.getLogger(__name__)

# A route an operator types answers at a number: the widget has none, and nothing dials it.
NOT_A_NUMBER = "an operator's route answers at a number, and the {channel} widget has none"


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


# A number belongs to one org in practice — an operator typed it because Meta or the carrier gave
# it to them — but nothing in the schema says so, and a silent pick between two orgs is a call
# answered by the wrong tenant. The oldest row answers, and the line names both so it is fixed.
TWO_ORGS = "%s answers in org %s: org %s typed the same number, and the older row answers"


def the_first_of(rows: list[Route]) -> Route | None:
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


def routes_for(pool: Pool | None) -> Routes:
    """Postgres when the process opened one; memory when it is a clone running on a dev key."""
    # Imported here: both adapters import this module for the port, and the one place that
    # picks between them is the one place the cycle would close (auth/members.py).
    from pinecall.routes.records_memory import MemoryRoutes
    from pinecall.routes.records_postgres import PostgresRoutes

    return MemoryRoutes() if pool is None else PostgresRoutes(pool)
