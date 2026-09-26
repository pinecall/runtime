"""How the widget presents an agent — name, line, greeting, colour, theme — per org and world."""

from __future__ import annotations

import re
from dataclasses import dataclass, fields
from typing import Any, Protocol

from pinecall.db import Pool
from pinecall.types import DeclarationRefused
from pinecall_protocol.rest import WidgetTheme

# What a page may carry through the widget's own attributes, bounded so one row is one screen.
LONGEST = {"title": 80, "tagline": 160, "greeting": 500, "accent": 40}

# A colour as CSS writes one: `#cd58b2`, `rebeccapurple`, `rgb(205 88 178)`. Nothing that could
# close a declaration and open another, because the widget sets it as a CSS variable.
_A_COLOUR = re.compile(
    r"^(#[0-9a-fA-F]{3,8}|[a-zA-Z]+|(rgb|rgba|hsl|hsla|oklch)\([0-9.,%\s/a-z-]+\))$"
)


@dataclass(frozen=True)
class Widget:
    """One agent's widget as the gateway keeps it. None is the widget's own default."""

    title: str | None = None
    tagline: str | None = None
    greeting: str | None = None
    accent: str | None = None
    autostart: bool = False
    theme: WidgetTheme | None = None

    def __post_init__(self) -> None:
        for name, longest in LONGEST.items():
            value: str | None = getattr(self, name)
            if value is not None and len(value) > longest:
                raise DeclarationRefused(f"a widget's {name} is {longest} characters at most")
        if self.accent is not None and not _A_COLOUR.match(self.accent):
            raise DeclarationRefused(f"an accent is a CSS colour, not {self.accent!r}")


class Widgets(Protocol):
    """Where the widget settings of an org's agents are kept, one row per org, world and agent."""

    async def of(self, org: str, env: str, agent: str) -> Widget:
        """This agent's widget, or the widget's defaults when nobody set one."""
        ...

    async def put(self, org: str, env: str, agent: str, widget: Widget) -> None:
        """Replace the agent's widget, whole."""
        ...


class MemoryWidgets:
    """A gateway with no pool: the settings live as long as the process."""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str, str], Widget] = {}

    async def of(self, org: str, env: str, agent: str) -> Widget:
        return self._rows.get((org, env, agent), Widget())

    async def put(self, org: str, env: str, agent: str, widget: Widget) -> None:
        self._rows[(org, env, agent)] = widget


COLUMNS = tuple(field.name for field in fields(Widget))

_OF = f"SELECT {', '.join(COLUMNS)} FROM agent_widgets WHERE org = $1 AND env = $2 AND agent = $3"

_PUT = f"""
INSERT INTO agent_widgets (org, env, agent, {", ".join(COLUMNS)})
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
ON CONFLICT (org, env, agent) DO UPDATE SET
    {", ".join(f"{column} = excluded.{column}" for column in COLUMNS)}, set_at = now()
"""


class PostgresWidgets:
    """The table 0029 made, and the column 0052 added."""

    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def of(self, org: str, env: str, agent: str) -> Widget:
        row: Any = await self._pool.fetchrow(_OF, org, env, agent)
        return Widget() if row is None else Widget(**{column: row[column] for column in COLUMNS})

    async def put(self, org: str, env: str, agent: str, widget: Widget) -> None:
        await self._pool.execute(
            _PUT, org, env, agent, *(getattr(widget, column) for column in COLUMNS)
        )


def widgets_for(pool: Pool | None) -> Widgets:
    """The table when this process opened a pool, and this process's own memory when it did not."""
    return MemoryWidgets() if pool is None else PostgresWidgets(pool)
