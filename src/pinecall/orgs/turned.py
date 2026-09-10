"""Where a knob an operator turned is kept, so a deploy does not give the agent back its class."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pinecall.log.store import Pool

# The five the pipeline door takes, in the order the table declares them. This module stores
# columns and never a meaning: what a knob is allowed to be, and what an empty one would do, is
# providers/overrides.py's, which is the one place that knows a vendor. The two meet structurally
# — `Keeps` there is satisfied by the classes here — because neither package may import the other.
KNOBS = ("stt", "llm", "voice", "tts_model", "greeting")

# One agent's whole set as it is stored: every knob, and None for one nobody turned.
type Knobs = Mapping[str, str | None]


class MemoryTurned:
    """A gateway with no pool: turned knobs live as long as the process, as they always did."""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], Knobs] = {}

    async def all(self) -> Mapping[str, Knobs]:
        return {agent: knobs for (_, agent), knobs in self._rows.items()}

    async def put(self, org: str, agent: str, knobs: Knobs) -> None:
        self._rows[(org, agent)] = dict(knobs)


class PostgresTurned:
    """The table. A slug belongs to one org for good, so what is read back is keyed by slug."""

    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def all(self) -> Mapping[str, Knobs]:
        rows: Sequence[Mapping[str, Any]] = await self._pool.fetch(
            f"SELECT agent, {', '.join(KNOBS)} FROM pipeline_overrides"
        )
        return {row["agent"]: {knob: row[knob] for knob in KNOBS} for row in rows}

    # One statement, whole: the door's PUT replaces the set, so a knob given back to the app has to
    # become NULL here and not merely stay as it was.
    async def put(self, org: str, agent: str, knobs: Knobs) -> None:
        columns = ", ".join(KNOBS)
        places = ", ".join(f"${at}" for at in range(3, 3 + len(KNOBS)))
        sets = ", ".join(f"{knob} = ${at}" for at, knob in enumerate(KNOBS, start=3))
        await self._pool.execute(
            f"INSERT INTO pipeline_overrides (org, agent, {columns}) VALUES ($1, $2, {places}) "
            f"ON CONFLICT (org, agent) DO UPDATE SET {sets}, set_at = now()",
            org,
            agent,
            *(knobs.get(knob) for knob in KNOBS),
        )


def turned_for(pool: Pool | None) -> MemoryTurned | PostgresTurned:
    """The table when this process opened a pool, and this process's own memory when it did not."""
    return MemoryTurned() if pool is None else PostgresTurned(pool)
