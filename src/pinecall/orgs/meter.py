"""The meter: every org's consumption, folded from the log as it grows, one cursor per process."""

from __future__ import annotations

import asyncio

from pinecall.log.replay import pages
from pinecall.log.store import DEFAULT_LIMIT, Metered, Store
from pinecall.log.usage import METERED_TYPES, Totals, fold_usage_row


# Nothing here is a table. The totals are a fold over the log's own call.summary and call.score
# rows, and the cursor is how far the fold got: a process that restarts folds from zero and lands
# on the same numbers, because the log is the truth and this is arithmetic over it. A quota check
# asks for one org's totals and pays for exactly the rows written since the last check.
class Meter:
    """Each org's totals so far, caught up from the log every time somebody asks."""

    def __init__(self, store: Store) -> None:
        self._store = store
        self._cursor = 0
        self._totals: dict[str, Totals] = {}
        # Two calls opening at once must not both fold the same page: one catches up, one waits.
        self._folding = asyncio.Lock()

    async def totals(self, org: str) -> Totals:
        """What this org has consumed as of the log's last metered row."""
        await self._catch_up()
        return self._totals.get(org, Totals())

    async def _catch_up(self) -> None:
        """Fold every metered row above the cursor, through the runtime's one paging loop."""
        async with self._folding:
            async for metered in pages(self._read, self._cursor, DEFAULT_LIMIT, position_of):
                row = fold_usage_row(metered)
                self._totals[row.org] = self._totals.get(row.org, Totals()).plus(row)
                self._cursor = row.cursor

    async def _read(self, after: int, limit: int) -> list[Metered]:
        """One page of the metered types across every log."""
        return await self._store.across(METERED_TYPES, after=after, limit=limit)


def position_of(metered: Metered) -> int:
    """The cursor a page across logs resumes from: the position, never a seq."""
    return metered.position
