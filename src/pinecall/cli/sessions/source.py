"""Where `sessions` reads from: one store, open on the database, and the three questions it asks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pinecall.log.entry import Entry
from pinecall.log.replay import whole
from pinecall.log.store.postgres import DEFAULT_SCHEMA, PostgresStore


class Calls(Protocol):
    """What a verb needs of the database. Source speaks Postgres; a test hands in its own."""

    async def calls(self, agent: str | None, limit: int) -> list[str]:
        """The newest calls, of one agent or of the whole database, newest first."""
        ...

    async def newest_live_call(self) -> str | None:
        """The call to follow when nobody named one, or None when every log is sealed."""
        ...

    async def entries(self, call: str, *, after: int = 0) -> list[Entry]:
        """One call's durable entries above the cursor, whole."""
        ...


@dataclass(frozen=True)
class Source:
    """The database as `sessions` sees it: a store, and nothing of the driver underneath it."""

    store: PostgresStore

    @classmethod
    async def open(cls, dsn: str, *, schema: str = DEFAULT_SCHEMA) -> Source:
        """A store of our own on this database. Close it with aclose(), whatever the verb did."""
        store = await PostgresStore.connect(dsn, schema=schema, min_size=1, max_size=2)
        return cls(store=store)

    async def aclose(self) -> None:
        """Give the pool back. A CLI that leaves a connection open is a CLI that hangs on exit."""
        await self.store.aclose()

    async def calls(self, agent: str | None, limit: int) -> list[str]:
        """The newest calls, of one agent or of the whole database, newest first."""
        return await self.store.newest_calls(limit, agent)

    async def newest_live_call(self) -> str | None:
        """The call `tail` follows when nobody named one: the newest log nothing has sealed."""
        return await self.store.newest_live_call()

    async def entries(self, call: str, *, after: int = 0) -> list[Entry]:
        """One call's durable entries above the cursor, whole: a page at a time until it ends."""
        return await whole(self.store, call, after=after)
