"""How the widget presents an agent — name, line, greeting, colour, theme — per org and world."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

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


def widgets_for(pool: Pool | None) -> Widgets:
    """The table when this process opened a pool, and this process's own memory when it did not."""
    # Imported here: both adapters import this module for the port, and the one place that
    # picks between them is the one place the cycle would close (auth/members.py).
    from pinecall.orgs.widgets_memory import MemoryWidgets
    from pinecall.orgs.widgets_postgres import PostgresWidgets

    return MemoryWidgets() if pool is None else PostgresWidgets(pool)
