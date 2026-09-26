"""The hold audio each org chose, in Postgres: the file's bytes and its name, one row an org."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pinecall.db import Pool
from pinecall.orgs.hold_melody import Chosen


class PostgresHoldAudio:
    """The table: one row per agent that was told otherwise, and none for the default."""

    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def chosen(self, org: str, agent: str) -> Chosen | None:
        row: Mapping[str, Any] | None = await self._pool.fetchrow(
            "SELECT played, sha256, seconds, name FROM hold_audio WHERE org = $1 AND agent = $2",
            org,
            agent,
        )
        if row is None:
            return None
        return Chosen(
            played=row["played"], sha256=row["sha256"], seconds=row["seconds"], name=row["name"]
        )

    async def audio(self, org: str, agent: str) -> bytes | None:
        row: Mapping[str, Any] | None = await self._pool.fetchrow(
            "SELECT audio FROM hold_audio WHERE org = $1 AND agent = $2", org, agent
        )
        return None if row is None or row["audio"] is None else bytes(row["audio"])

    async def keep(self, org: str, agent: str, chosen: Chosen, audio: bytes | None) -> None:
        await self._pool.execute(
            "INSERT INTO hold_audio (org, agent, played, audio, sha256, seconds, name) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7) "
            "ON CONFLICT (org, agent) DO UPDATE SET played = $3, audio = $4, sha256 = $5, "
            "seconds = $6, name = $7, set_at = now()",
            org,
            agent,
            chosen.played,
            audio,
            chosen.sha256,
            chosen.seconds,
            chosen.name,
        )

    async def forget(self, org: str, agent: str) -> None:
        await self._pool.execute("DELETE FROM hold_audio WHERE org = $1 AND agent = $2", org, agent)
