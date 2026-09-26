"""The box's settings in Postgres: one row a setting, secrets sealed under the box's key."""

from __future__ import annotations

import json
from typing import Any

from pinecall.db import Pool
from pinecall.orgs.box_settings import BoxSetting, opened, sealed
from pinecall.orgs.vault import Cipher

# This pool's connections were never taught the jsonb codec (only the log's own are), so jsonb is
# text going out and text coming back: evals/run_store.py says the same.
_PUT = """
INSERT INTO box_settings (name, value, ciphertext, set_at) VALUES ($1, $2::jsonb, $3, now())
    ON CONFLICT (name) DO UPDATE
    SET value = excluded.value, ciphertext = excluded.ciphertext, set_at = now()
"""

_OF = "SELECT value::text AS value, ciphertext FROM box_settings WHERE name = $1"

_DROP = "DELETE FROM box_settings WHERE name = $1 RETURNING name"

_NOTED = "UPDATE box_settings SET value = value || $2::jsonb WHERE name = $1"


class PostgresBoxSettings:
    """The table in Postgres, read each time it is asked: what is set now is what is used next."""

    def __init__(self, pool: Pool, cipher: Cipher | None) -> None:
        self._pool = pool
        self._cipher = cipher

    async def put(self, name: str, value: dict[str, Any], secret: str | None = None) -> None:
        """The secret reaches this method in the clear and nothing under it: a token is kept."""
        await self._pool.execute(_PUT, name, json.dumps(value), sealed(self._cipher, secret))

    async def of(self, name: str) -> BoxSetting | None:
        """One read on the primary key."""
        row = await self._pool.fetchrow(_OF, name)
        if row is None:
            return None
        ciphertext = row["ciphertext"]
        return BoxSetting(
            json.loads(str(row["value"])),
            opened(self._cipher, None if ciphertext is None else str(ciphertext)),
        )

    async def drop(self, name: str) -> bool:
        """The command tag says whether a row went, so dropping nothing is told apart."""
        return await self._pool.fetchrow(_DROP, name) is not None

    async def noted(self, name: str, changes: dict[str, Any]) -> None:
        """One UPDATE: a setting nobody made has no row and nothing is written."""
        await self._pool.execute(_NOTED, name, json.dumps(changes))
