"""Which melody an agent plays while a tool runs, when it is not the one every agent ships with."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

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


class HoldAudio(Protocol):
    """Where each agent's hold choice is kept: what was chosen, and the clip's bytes when custom."""

    async def chosen(self, org: str, agent: str) -> Chosen | None:
        """The agent's choice, or None when it plays the runtime's own melody."""
        ...

    async def audio(self, org: str, agent: str) -> bytes | None:
        """The uploaded clip's bytes, or None when the agent has no clip."""
        ...

    async def keep(self, org: str, agent: str, chosen: Chosen, audio: bytes | None) -> None:
        """Replace the agent's choice, whole, with the clip when it is one."""
        ...

    async def forget(self, org: str, agent: str) -> None:
        """Back to the runtime's own melody: the row, and the clip with it, gone."""
        ...


def hold_audio_for(pool: Pool | None) -> HoldAudio:
    """The table when there is a database, and the process's own memory when there is none."""
    # Imported here: both adapters import this module for the port, and the one place that
    # picks between them is the one place the cycle would close (auth/members.py).
    from pinecall.orgs.hold_melody_memory import MemoryHoldAudio
    from pinecall.orgs.hold_melody_postgres import PostgresHoldAudio

    return MemoryHoldAudio() if pool is None else PostgresHoldAudio(pool)
