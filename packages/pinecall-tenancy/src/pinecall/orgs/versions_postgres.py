"""Versions in Postgres: one table's five statements, run through the shape's own reader."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from pinecall.db import Pool
from pinecall.orgs.versions import Corner, Statements, VersionMoved
from pinecall.types import Kept


# A write is one INSERT whose version is born inside it: the aggregate over the corner yields
# one row even over none, HAVING is the if_version gate, and two writers computing the same
# number both hit the primary key — the first lands, the second's RETURNING is empty. No lock,
# no transaction, and no driver exception here, because this package may not name the driver
# (db/pool.py).
class PostgresVersions[T]:
    """One table in Postgres, read through its statements and the shape's own reader."""

    def __init__(
        self,
        pool: Pool,
        statements: Statements,
        read: Callable[[Mapping[str, Any]], Kept[T]],
        columns: Callable[[T], tuple[Any, ...]],
    ) -> None:
        self._pool = pool
        self._statements = statements
        self._read = read
        self._columns = columns

    async def own(self, corner: Corner) -> Kept[T] | None:
        row = await self._pool.fetchrow(self._statements.own, *corner.args)
        return None if row is None else self._read(row)

    async def chain(self, corner: Corner) -> list[Kept[T]]:
        rows = await self._pool.fetch(self._statements.chain, *corner.args)
        return [self._read(row) for row in rows]

    async def at(self, corner: Corner, version: int) -> Kept[T] | None:
        row = await self._pool.fetchrow(self._statements.at, *corner.args, version)
        return None if row is None else self._read(row)

    async def history(self, corner: Corner, limit: int) -> list[Kept[T]]:
        rows = await self._pool.fetch(self._statements.history, *corner.args, limit)
        return [self._read(row) for row in rows]

    async def every_chain(self, org: str, env: str, holder: str) -> dict[str, list[Kept[T]]]:
        if self._statements.every_chain is None:
            raise TypeError("this table has no agent column to chain every agent by")
        chains: dict[str, list[Kept[T]]] = {}
        for row in await self._pool.fetch(self._statements.every_chain, org, env, holder):
            chains.setdefault(str(row["agent"]), []).append(self._read(row))
        return chains

    async def put(
        self, corner: Corner, value: T, *, author: str, note: str | None, if_version: int | None
    ) -> int:
        row = await self._pool.fetchrow(
            self._statements.put, *corner.args, *self._columns(value), author, note, if_version
        )
        if row is None:
            standing = await self.own(corner)
            raise VersionMoved(0 if standing is None else standing.version)
        return int(row["version"])
