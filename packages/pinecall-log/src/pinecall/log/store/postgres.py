"""The Postgres Store: the log's entries in their table, over a pool db/ opened."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from pinecall.db.connecting import DEFAULT_SCHEMA, create_pool
from pinecall.db.pool import Connection, Pool
from pinecall.log.call_facts import change_of
from pinecall.log.entry import Entry
from pinecall.log.store.call_index_postgres import PostgresIndex
from pinecall.log.store.call_index_sql import RESCORED
from pinecall.log.store.protocol import DEFAULT_LIMIT, LogSealed, Metered
from pinecall.log.store.store_sql import (
    ACROSS,
    APPEND,
    CALLS_NEWEST_FIRST,
    CALLS_OF,
    LATEST_SEQ,
    LIST_CALLS,
    MOVED,
    NEWEST_LIVE_CALL,
    OWNED,
    OWNER,
    PAGE,
    SEAL,
)
from pinecall.types import Versions
from pinecall.types.json import JsonObject

# An agent's own log is a log like any other, under a name no call id can wear (see the CHECK
# constraint in 0001). The database computes the same string for every row it stores.
AGENT_LOG_PREFIX = "@"


logger = logging.getLogger(__name__)


class PostgresStore(PostgresIndex):
    """A Store on one pool. Seq and ts are born in append(); ephemerals get a seq and no row."""

    def __init__(
        self,
        pool: Pool,
        *,
        clock: Callable[[], float] = time.time,
        owns_pool: bool = False,
    ) -> None:
        self._pool = pool
        # The entry's ts is the appending process's wall clock, not the database's: the ts of an
        # entry is when the runtime saw the thing happen, and the database is another machine.
        # A test hands its own clock in, exactly as it does to MemoryStore.
        self._clock = clock
        self._owns_pool = owns_pool

    @classmethod
    async def connect(
        cls,
        dsn: str,
        *,
        schema: str = DEFAULT_SCHEMA,
        clock: Callable[[], float] = time.time,
        min_size: int = 1,
        max_size: int = 10,
    ) -> PostgresStore:
        """Open a pool of this store's own. Close it with aclose()."""
        pool = await create_pool(
            dsn, schema=schema, jsonb_as_dicts=True, min_size=min_size, max_size=max_size
        )
        return cls(pool, clock=clock, owns_pool=True)

    async def aclose(self) -> None:
        """Give the pool back, if this store opened it. A borrowed pool is the caller's to close."""
        if self._owns_pool:
            await self._pool.close()

    async def append(
        self,
        call: str | None,
        agent: str,
        type: str,
        data: JsonObject,
        ephemeral: bool = False,
    ) -> Entry:
        """The next seq of the log, in one statement. An ephemeral gets its seq and no row."""
        ts = self._clock()
        async with self._pool.acquire() as connection, connection.transaction():
            seq: int | None = await connection.fetchval(
                APPEND, log_name(call, agent), agent, call, ts, type, ephemeral, data
            )
            if seq is None:
                raise LogSealed(f"call {call} has ended: {type} cannot be appended")
            entry = Entry(
                seq=seq, ts=ts, call=call, agent=agent, type=type, ephemeral=ephemeral, data=data
            )
            await self._indexed(connection, entry)
        return entry

    async def rescored(self, call: str, agent: str, data: JsonObject) -> Entry:
        """A verdict onto a call whose log already sealed: the one entry a sealed log takes."""
        ts = self._clock()
        async with self._pool.acquire() as connection, connection.transaction():
            seq: int | None = await connection.fetchval(RESCORED, call, agent, ts, data)
            if seq is None:
                raise LogSealed(f"call {call} has no log to judge")
            entry = Entry(
                seq=seq,
                ts=ts,
                call=call,
                agent=agent,
                type="call.score",
                ephemeral=False,
                data=data,
            )
            await self._indexed(connection, entry)
        return entry

    # The fold runs AFTER the entry is written and never instead of it: a row that failed to
    # change is a list that says less, while an entry that failed to write is a call that lost a
    # fact. Both on the ONE connection the append holds, inside its transaction: the head row's
    # lock is held until the fold has landed, so two appends to one call fold in seq order — on
    # separate pooled connections they could land the other way round, and `last_text` said the
    # older one (2026-09-26). The fold is a savepoint of its own, so its failure rolls back the
    # fold and never the entry.
    async def _indexed(self, connection: Connection, entry: Entry) -> None:
        """The call's facts given what the entry said; a fold that broke is logged and dropped."""
        change = change_of(entry)
        if entry.call is None or change is None:
            return
        try:
            async with connection.transaction():
                await self._fold(connection, entry.call, change)
        except Exception:
            logger.warning(
                "call %s: its facts did not fold %s", entry.call, entry.type, exc_info=True
            )

    async def since(self, call: str, after: int = 0, limit: int = DEFAULT_LIMIT) -> list[Entry]:
        """The call's durable entries above the cursor. Ephemerals were never written: holes."""
        return await self._page(call, after, limit)

    async def agent_since(
        self, agent: str, after: int = 0, limit: int = DEFAULT_LIMIT
    ) -> list[Entry]:
        """The agent's own log above the cursor, out of the same table under its own name."""
        return await self._page(log_name(None, agent), after, limit)

    async def seal(self, call: str) -> None:
        """Raise the flag the append statement checks. Sealing twice is still sealed."""
        await self._pool.execute(SEAL, call)

    async def list_calls(self, agent: str) -> list[str]:
        """Every call this agent opened a log for, oldest first, read off the head rows."""
        rows: Sequence[Mapping[str, Any]] = await self._pool.fetch(LIST_CALLS, agent)
        return [str(row["call"]) for row in rows]

    async def calls_of(
        self,
        org: str,
        limit: int,
        env: str | None = None,
        holder: str | None = None,
        agent: str | None = None,
    ) -> list[str]:
        """The org's newest calls, off the head rows, cut to a world, a corner or an agent."""
        rows: Sequence[Mapping[str, Any]] = await self._pool.fetch(
            CALLS_OF, org, limit, env, holder, agent
        )
        return [str(row["call"]) for row in rows]

    async def latest_seq(self, call: str) -> int:
        """The highest seq the log handed out, ephemerals counted; 0 for a call nobody wrote to."""
        seq: int | None = await self._pool.fetchval(LATEST_SEQ, call)
        return int(seq or 0)

    async def owned(
        self,
        call: str | None,
        agent: str,
        org: str,
        env: str | None = None,
        holder: str | None = None,
        versions: Versions | None = None,
    ) -> None:
        """Write the owner, a call's corner and the versions it ran on, on the head row, creating
        it when the claim comes before any entry. An agent's own log has no corner: one log per
        slug, whatever the world."""
        corner = None if call is None or env is None else (env, holder or "")
        built_on = Versions() if versions is None or call is None else versions
        await self._pool.execute(
            OWNED,
            log_name(call, agent),
            agent,
            call,
            org,
            None if corner is None else corner[0],
            None if corner is None else corner[1],
            built_on.config,
            built_on.lexicon,
        )

    async def moved(self, agent: str, org: str) -> int:
        """Every head row of this agent, into another org, and how many there were."""
        row = await self._pool.fetchrow(MOVED, agent, org)
        return 0 if row is None else int(row["moved"])

    async def owner(self, call: str | None, agent: str) -> str | None:
        """Whose log this is; None for a log with no head row, or one nobody claimed."""
        org: str | None = await self._pool.fetchval(OWNER, log_name(call, agent))
        return None if org is None else str(org)

    async def across(
        self, types: Sequence[str], after: int = 0, limit: int = DEFAULT_LIMIT
    ) -> list[Metered]:
        """One page across every log: the database filters, orders and cuts, never us."""
        rows: Sequence[Mapping[str, Any]] = await self._pool.fetch(
            ACROSS, list(types), max(after, 0), max(limit, 0)
        )
        return [
            Metered(position=int(row["position"]), org=row["org"], entry=entry_of_row(row))
            for row in rows
        ]

    async def newest_calls(self, limit: int, agent: str | None = None) -> list[str]:
        """The newest calls, of one agent or of all: the database orders and cuts, never us."""
        rows: Sequence[Mapping[str, Any]] = await self._pool.fetch(CALLS_NEWEST_FIRST, limit, agent)
        return [str(row["call"]) for row in rows]

    async def newest_live_call(self) -> str | None:
        """The newest log nothing has sealed, or None when every call has ended."""
        rows: Sequence[Mapping[str, Any]] = await self._pool.fetch(NEWEST_LIVE_CALL)
        return str(rows[0]["call"]) if rows else None

    async def _page(self, log: str, after: int, limit: int) -> list[Entry]:
        """One page of one log, by the identity the database computes for every row."""
        rows: Sequence[Mapping[str, Any]] = await self._pool.fetch(
            PAGE, log, max(after, 0), max(limit, 0)
        )
        return [entry_of_row(row) for row in rows]


def log_name(call: str | None, agent: str) -> str:
    """A log's identity, the same string the database generates: the call, or @ and the agent."""
    return call if call is not None else f"{AGENT_LOG_PREFIX}{agent}"


def entry_of_row(row: Mapping[str, Any]) -> Entry:
    """One row back into the envelope. No conversion: the columns are the envelope's fields."""
    return Entry(
        seq=int(row["seq"]),
        ts=float(row["ts"]),
        call=row["call"],
        agent=str(row["agent"]),
        type=str(row["type"]),
        ephemeral=bool(row["ephemeral"]),
        data=dict(row["data"]),
    )
