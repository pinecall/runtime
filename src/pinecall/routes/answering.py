"""Who answers a door when two tables name it: the operator's row wins, and the loser is named."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from pinecall.routes.table import Routes
from pinecall.types import Route

logger = logging.getLogger(__name__)

# The whole point of `routes add` is that a number moves with no deploy. A declaration that could
# take it back on the app's next restart would make the verb a lie, so the app loses the door —
# and is told, by name, every time the two tables are read together. It stops when it is fixed.
TAKEN = "%s answers for agent %s: the operator's route outranks agent %s, which declared it too"

# Which of the two tables put a door in the answer. An operator reads this; the worker never does.
type Source = Literal["operator", "app"]


class Declaration(Protocol):
    """One agent as an app holds it, as far as a door is concerned: whose it is, which doors."""

    @property
    def org(self) -> str: ...

    @property
    def agent(self) -> str: ...

    @property
    def routes(self) -> tuple[Route, ...]: ...


# The apps' side of the answer, as the api's registry keeps it. A Protocol, so the two tables
# that read declarations beside the operator's rows never import the process that holds them.
class Declaring(Protocol):
    """What the connected apps declared: every door of an org, and who answers at one door."""

    def routes(self, org: str) -> tuple[Route, ...]:
        """Every door this org answers right now, in the order its agents claimed them."""
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
async def answered(org: str, registry: Declaring, table: Routes) -> tuple[Answering, ...]:
    """Both tables of one org, resolved: what is typed, and what is declared and still free."""
    return doors(await table.of_org(org), registry.routes(org))


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
