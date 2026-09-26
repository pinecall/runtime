"""The hold audio each org chose, in this process's memory."""

from __future__ import annotations

from pinecall.orgs.hold_melody import Chosen


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
