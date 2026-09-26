"""Which melody an agent plays while a tool runs, when it is not the one every agent ships with."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pinecall.db import Pool

# No row is the runtime's own melody. `off` is silence; `custom` is a clip somebody uploaded,
# kept as the runtime converted it. The meaning of the bytes is session/hold_melody.py's.
type Played = Literal["off", "custom"]


@dataclass(frozen=True)
class Chosen:
    """One agent's choice, as stored: off, or a clip with its hash, its length and its file name."""

    played: Played
    sha256: str | None = None
    seconds: float | None = None
    name: str | None = None


class MemoryHoldAudio:
    """A gateway with no pool: the choice lives as long as the process, like the turned knobs."""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], tuple[Chosen, bytes | None]] = {}

    async def chosen(self, org: str, agent: str) -> Chosen | None:
        row = self._rows.get((org, agent))
        return None if row is None else row[0]

    async def audio(self, org: str, agent: str) -> bytes | None:
        row = self._rows.get((org, agent))
        return None if row is None else row[1]

    async def keep(self, org: str, agent: str, chosen: Chosen, audio: bytes | None) -> None:
        self._rows[(org, agent)] = (chosen, audio)

    async def forget(self, org: str, agent: str) -> None:
        self._rows.pop((org, agent), None)


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


type HoldAudio = MemoryHoldAudio | PostgresHoldAudio


def hold_audio_for(pool: Pool | None) -> HoldAudio:
    """The table when there is a database, and the process's own memory when there is none."""
    return MemoryHoldAudio() if pool is None else PostgresHoldAudio(pool)
