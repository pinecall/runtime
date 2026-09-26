"""Rows kept a version at a time per corner, in memory or Postgres: what a tuning and a lexicon share."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from pinecall._exceptions import PinecallError
from pinecall.log.store import Pool
from pinecall.orgs.resolving import corners
from pinecall.types import Kept

# What a write says when the corner is not at the version the writer read. Two people saving the
# same agent from two screens: the second is told, never quietly written over the first.
MOVED = "this corner is at v{newest} now, not the version you read: read it again, then set again"


class VersionMoved(PinecallError):
    """The corner's newest is not the one the writer saw, or another writer got there first."""

    def __init__(self, newest: int) -> None:
        super().__init__(MOVED.format(newest=newest))
        self.newest = newest


@dataclass(frozen=True)
class Corner:
    """Whose rows: the org, the world, the holder as the column spells it, and the agent when the
    rows are one agent's (a tuning) and not the org's (a lexicon)."""

    org: str
    env: str
    holder: str
    agent: str | None = None

    @property
    def args(self) -> tuple[str, ...]:
        """The corner as a statement takes it: $1 org, $2 env, $3 holder, then the agent."""
        return (self.org, self.env, self.holder) + (() if self.agent is None else (self.agent,))

    def held_by(self, holder: str) -> Corner:
        """The same rows in another holder's corner."""
        return dataclasses.replace(self, holder=holder)


class Versions[T](Protocol):
    """One table of versions: the newest of a corner, one version, the history, and a write."""

    async def own(self, corner: Corner) -> Kept[T] | None:
        """This corner's newest and nothing else's; None when it set nothing."""
        ...

    async def chain(self, corner: Corner) -> list[Kept[T]]:
        """The newest of each corner this one reads through, nearest first: its own, the org's."""
        ...

    async def at(self, corner: Corner, version: int) -> Kept[T] | None:
        """One version, the corner's own if it has it, else the org's own."""
        ...

    async def history(self, corner: Corner, limit: int) -> list[Kept[T]]:
        """This corner's versions, newest first."""
        ...

    async def every_chain(self, org: str, env: str, holder: str) -> dict[str, list[Kept[T]]]:
        """Every agent's chain in this world, by slug, each as this corner reads it."""
        ...

    async def put(
        self, corner: Corner, value: T, *, author: str, note: str | None, if_version: int | None
    ) -> int:
        """A new version in this corner, numbered after its last; VersionMoved when it moved."""
        ...


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


@dataclass(frozen=True)
class Statements:
    """One table's five statements, each taking the corner's args first (Corner.args), then
    `at`'s version, `history`'s limit, `put`'s columns, author, note and if_version."""

    own: str
    chain: str
    at: str
    history: str
    put: str
    # Every agent's chain in the world: $1 org, $2 env, $3 holder. None for a table with no agent.
    every_chain: str | None = None


# A write is one INSERT whose version is born inside it: the aggregate over the corner yields
# one row even over none, HAVING is the if_version gate, and two writers computing the same
# number both hit the primary key — the first lands, the second's RETURNING is empty. No lock,
# no transaction, and no driver exception here, because this package may not name the driver
# (log/store/pool.py).
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
