"""Versions in this process's memory: a corner's rows, newest last, for as long as it runs."""

from __future__ import annotations

from datetime import UTC, datetime

from pinecall.orgs.tuning_resolution import corners
from pinecall.orgs.versions import Corner, VersionMoved
from pinecall.types import Kept


class MemoryVersions[T]:
    """A gateway with no pool: the versions live as long as the process."""

    def __init__(self) -> None:
        self._rows: dict[Corner, list[Kept[T]]] = {}

    async def own(self, corner: Corner) -> Kept[T] | None:
        rows = self._rows.get(corner, [])
        return rows[-1] if rows else None

    async def chain(self, corner: Corner) -> list[Kept[T]]:
        found = [await self.own(corner.held_by(holder)) for holder in corners(corner.holder)]
        return [row for row in found if row is not None]

    async def at(self, corner: Corner, version: int) -> Kept[T] | None:
        for holder in corners(corner.holder):
            for row in self._rows.get(corner.held_by(holder), []):
                if row.version == version:
                    return row
        return None

    async def history(self, corner: Corner, limit: int) -> list[Kept[T]]:
        return list(reversed(self._rows.get(corner, [])))[:limit]

    async def every_chain(self, org: str, env: str, holder: str) -> dict[str, list[Kept[T]]]:
        agents = {
            kept.agent
            for kept in self._rows
            if (kept.org, kept.env) == (org, env) and kept.agent is not None
        }
        chains = {slug: await self.chain(Corner(org, env, holder, slug)) for slug in sorted(agents)}
        return {slug: chain for slug, chain in chains.items() if chain}

    async def put(
        self, corner: Corner, value: T, *, author: str, note: str | None, if_version: int | None
    ) -> int:
        """The memory store's INSERT: the same if_version gate the statement has, with no await."""
        rows = self._rows.setdefault(corner, [])
        standing = len(rows)
        if if_version is not None and if_version != standing:
            raise VersionMoved(standing)
        rows.append(Kept(corner.holder, standing + 1, author, note, datetime.now(UTC), value))
        return standing + 1
