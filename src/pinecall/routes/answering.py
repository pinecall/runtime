"""Who answers a door when two tables name it: the operator's row wins, and the loser is named."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from pinecall.routes.table import Routes
from pinecall.types import PRODUCTION, SANDBOX, Channel, Env, Route

logger = logging.getLogger(__name__)

# The whole point of `routes add` is that a number moves with no deploy. A declaration that could
# take it back on the app's next restart would make the verb a lie, so the app loses the door —
# and is told, by name, every time the two tables are read together. It stops when it is fixed.
TAKEN = "%s answers for agent %s: the operator's route outranks agent %s, which declared it too"

# Production first, so the number a tenant would name is the first one a plan or a fence shows.
ENVS_IN_ORDER: tuple[Env, ...] = (PRODUCTION, SANDBOX)

# Which of the two tables put a door in the answer. An operator reads this; the worker never does.
type Source = Literal["operator", "app"]


class Declaration(Protocol):
    """One agent as an app holds it, as far as a door is concerned: whose it is, which doors."""

    @property
    def slug(self) -> str: ...

    @property
    def org(self) -> str: ...

    @property
    def routes(self) -> tuple[Route, ...]: ...


# The apps' side of the answer, as the api's registry keeps it. A Protocol, so the two tables
# that read declarations beside the operator's rows never import the process that holds them.
class Declaring(Protocol):
    """What the connected apps declared: every door of an org, and who answers at one door."""

    def routes(self, org: str, env: Env, holder: str | None = None) -> tuple[Route, ...]:
        """Every door this org answers in this world right now, as its agents claimed them: the
        org's own corner, and this holder's when one is named."""
        ...

    def at(self, channel: str, number: str | None) -> Declaration | None:
        """Who answers this door: the number a call arrives on, resolved to one agent."""
        ...


@dataclass(frozen=True)
class Answering:
    """One door the org answers right now, and which table said so."""

    route: Route
    source: Source


# The one question both doors that need an org's routes ask — the worker's GET /v1/routes and the
# token door, which mints only for an agent the org answers on the web — so it is asked here.
# `holder` is whose corner of the world the declared half is read from: a developer's key in
# the sandbox sees the doors their own `pinecall start` declared, and everybody sees the org's.
async def answered(
    org: str, env: Env, registry: Declaring, table: Routes, holder: str | None = None
) -> tuple[Answering, ...]:
    """Both tables of one org in one world: what is typed, and what is declared and still free."""
    return doors(await table.of_org(org, env), registry.routes(org, env, holder))


# The other question, asked from the other side: a phone call arrived at a number and nobody has
# said whose it is. A number is one org's door in one world, so the one worker every org shares
# asks for it across every org — the operator's row first, as it wins everywhere, then whatever a
# connected app declared. This is the worker's question alone: a tenant's key never asks it.
async def at(channel: Channel, number: str, registry: Declaring, table: Routes) -> Answering | None:
    """The one door that answers this number on this channel, whichever org typed or declared it."""
    row = await table.at(channel, number)
    if row is not None:
        return Answering(row, "operator")
    declared = registry.at(channel, number)
    if declared is None:
        return None
    route = next((one for one in declared.routes if one.door == (channel, number)), None)
    return None if route is None else Answering(route, "app")


def doors(stored: Sequence[Route], declared: Sequence[Route]) -> tuple[Answering, ...]:
    """Every door of one org: the operator's rows, then the declarations no row has claimed."""
    for row, lost in overridden(stored, declared):
        logger.warning(TAKEN, row.number, row.agent, lost.agent)
    taken = {route.door for route in stored}
    return (
        *(Answering(route, "operator") for route in stored),
        *(Answering(route, "app") for route in declared if route.door not in taken),
    )


def overridden(
    stored: Sequence[Route], declared: Sequence[Route]
) -> tuple[tuple[Route, Route], ...]:
    """Each operator row that took a door from another agent, with the declaration it took."""
    at = {route.door: route for route in declared}
    return tuple(
        (row, at[row.door]) for row in stored if row.door in at and at[row.door].agent != row.agent
    )


# A number is the org's in either world — a route moves between them and the carrier never
# notices — so the trunk that may show one, and the country fence worked out from them, read
# both. It is here rather than beside either door because two of them ask: the outbound trunk's
# `numbers`, and the guards that fence a dial by the countries the org already answers in.
async def own_numbers(table: Routes, org: str) -> tuple[str, ...]:
    """Every phone number this org answers at, both worlds, in the order the table holds them."""
    found: list[str] = []
    for env in ENVS_IN_ORDER:
        for route in await table.of_org(org, env):
            if route.channel == "phone" and route.number is not None and route.number not in found:
                found.append(route.number)
    return tuple(found)
