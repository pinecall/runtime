"""Widgets in Postgres: one row per agent's widget, read on every page that loads it."""

from __future__ import annotations

from dataclasses import fields
from typing import Any

from pinecall.db import Pool
from pinecall.orgs.widgets import Widget

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
