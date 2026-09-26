"""Routes in this process's memory: every number an org answers at, and the agent it rings."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from pinecall.routes.records import Moved, door_of, the_first_of
from pinecall.types import Channel, Env, Route


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
        return the_first_of(
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
