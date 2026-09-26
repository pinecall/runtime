"""Widgets in this process's memory: the port's spec by example, and a gateway with no database."""

from __future__ import annotations

from pinecall.orgs.widgets import Widget


class MemoryWidgets:
    """A gateway with no pool: the settings live as long as the process."""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str, str], Widget] = {}

    async def of(self, org: str, env: str, agent: str) -> Widget:
        return self._rows.get((org, env, agent), Widget())

    async def put(self, org: str, env: str, agent: str, widget: Widget) -> None:
        self._rows[(org, env, agent)] = widget
