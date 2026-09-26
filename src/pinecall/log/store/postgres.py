"""The Postgres Store: log/'s one door to a driver. Every rule above it stays pure."""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit, urlunsplit

import asyncpg  # type: ignore[import-untyped]  # pyright: ignore[reportMissingTypeStubs]

from pinecall._exceptions import PinecallError
from pinecall.log.call_facts import change_of
from pinecall.log.entry import Entry
from pinecall.log.store.call_index_postgres import PostgresIndex
from pinecall.log.store.call_index_sql import RESCORED
from pinecall.log.store.pool import Connection, Pool
from pinecall.log.store.protocol import DEFAULT_LIMIT, LogSealed, Metered
from pinecall.log.store.store_sql import (
    ACROSS,
    APPEND,
    CALLS_NEWEST_FIRST,
    CALLS_OF,
    INSTALLED_EXTENSIONS,
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

# The .sql files, numbered, applied in name order. A migration is added, never edited. They are the
# RUNTIME's — the log's tables, auth's api_keys, memory's contact_memories (0008) and the
# knowledge base's two tables (0009) sit in one schema, applied by one runner — and only the
# runner that applies them lives here, behind the store's one door to the driver.
MIGRATIONS = Path(__file__).parents[2] / "migrations"

# A schema name reaches SQL as an identifier, where no parameter can go. So it is checked here
# rather than quoted there: a name that is not a plain lowercase word never becomes SQL at all.
_A_SCHEMA_NAME = re.compile(r"^[a-z_][a-z0-9_]*$")

# Postgres's default schema, spelled once: a store may be pointed at another (the tests own one
# each), and nothing else in the runtime needs to know what the default is called.
DEFAULT_SCHEMA = "public"

# An agent's own log is a log like any other, under a name no call id can wear (see the CHECK
# constraint in 0001). The database computes the same string for every row it stores.
AGENT_LOG_PREFIX = "@"


# asyncpg ships no py.typed, so a strict checker reads every call into it as Unknown. Two casts at
# the door keep the rest of this file typed, and nothing untyped leaves a method.
_create_pool = cast(
    "Callable[..., Awaitable[Any]]",
    asyncpg.create_pool,  # pyright: ignore[reportUnknownMemberType]
)
connect = cast(
    "Callable[..., Awaitable[Any]]",
    asyncpg.connect,  # pyright: ignore[reportUnknownMemberType]
)


class SchemaRefused(PinecallError):
    """A schema name that is not a plain lowercase word. It would have been spliced into SQL."""


# Every way a database can fail to open, under one name, raised from the one module that may say
# the driver's. A caller deciding whether to fall back must not have to import asyncpg to ask.
class StoreUnreachable(PinecallError):
    """The database did not answer, or answered that this is not a database we can use."""


# A DSN carries the password, and this sentence is printed in a terminal, a journal and an issue:
# `migrate` printed `postgresql://pinecall:pinecall@…` at a person the day the box's password
# changed (2026-09-20). Every refusal that names the database names it through here.
def without_password(dsn: str) -> str:
    """The DSN as it may be shown: the user, the host, the database — never the password."""
    parts = urlsplit(dsn)
    if parts.password is None:
        return dsn
    host = _bracketed(parts.hostname or "")
    if parts.port is not None:
        host = f"{host}:{parts.port}"
    netloc = f"{parts.username}@{host}" if parts.username else host
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _bracketed(host: str) -> str:
    """urlsplit hands back an IPv6 host without its brackets, and `::1:5432` is not an address."""
    return f"[{host}]" if ":" in host else host


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
        try:
            pool = await _create_pool(
                dsn,
                min_size=min_size,
                max_size=max_size,
                init=_teach_the_connection_json,
                server_settings={"search_path": search_path_of(schema)},
            )
        except (OSError, ValueError, asyncpg.PostgresError) as refused:
            raise StoreUnreachable(f"{without_password(dsn)}: {refused}") from refused
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


async def installed_extensions(dsn: str, *, timeout: float | None = None) -> set[str]:
    """Which extensions this database has. One connection, one query, closed either way."""
    connection: Any = await connect(dsn, timeout=timeout)
    try:
        rows: Sequence[Any] = await connection.fetch(INSTALLED_EXTENSIONS)
    finally:
        await connection.close()
    return {str(row["extname"]) for row in rows}


# The gateway reads API keys through a pool of its own, off a table this package knows nothing
# about. It still gets its pool from here, because this module is the one place that may name the
# driver — a second import of asyncpg is a second door to close.
async def create_pool(dsn: str, *, schema: str = DEFAULT_SCHEMA) -> Pool:
    """A plain connection pool, opened by the one module allowed to say the driver's name."""
    try:
        # The driver's pool answers our Protocol; the driver types it as nothing at all.
        opened = await _create_pool(dsn, server_settings={"search_path": search_path_of(schema)})
        return cast("Pool", opened)
    except (OSError, ValueError, asyncpg.PostgresError) as refused:
        # The same three the store's own connect turns into StoreUnreachable: a caller that opens
        # a pool must be able to say "no database answered" without naming the driver.
        raise StoreUnreachable(f"{without_password(dsn)}: {refused}") from refused


async def _teach_the_connection_json(connection: Any) -> None:
    """jsonb comes back as a dict and goes out as one; the store never sees a JSON string."""
    await connection.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )


# A schema of its own is what a test process gets, and its tables land there. The extensions'
# types and operators — halfvec, <=>, bm25's <@> — live where CREATE EXTENSION put them, in
# public, and a path that hides public cannot name a column of that type. So the schema comes
# first, where DDL creates, and public after it, where the types are found. The default schema is
# public itself and needs no second entry.
def search_path_of(schema: str) -> str:
    """The search path a schema is worked in: itself, then public, where the extensions are."""
    name = a_schema_name(schema)
    return name if name == DEFAULT_SCHEMA else f"{name}, {DEFAULT_SCHEMA}"


def a_schema_name(schema: str) -> str:
    """A schema is an identifier and cannot be a parameter, so it is checked before it is SQL."""
    if not _A_SCHEMA_NAME.match(schema):
        raise SchemaRefused(f"a schema name is a lowercase word, not {schema!r}")
    return schema


async def open_pool(database_url: str, *, schema: str = DEFAULT_SCHEMA) -> Pool:
    """The pool the gateway holds for its whole life. The lifespan that opened it closes it."""
    return await create_pool(database_url, schema=schema)
