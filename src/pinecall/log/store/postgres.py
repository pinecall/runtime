"""The Postgres Store: log/'s one door to a driver. Every rule above it stays pure."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any, cast

import asyncpg  # type: ignore[import-untyped]  # pyright: ignore[reportMissingTypeStubs]

from pinecall._exceptions import PinecallError
from pinecall.log.entry import Entry
from pinecall.log.store.protocol import DEFAULT_LIMIT, LogSealed, Metered
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

# ── the statements ──────────────────────────────────────────────────────────────

# One statement, one seq. The head row is inserted or bumped under its own row lock, so two
# appends racing queue up instead of reading one number twice; the entry is written in the same
# statement from the number that came out. A sealed log matches no WHERE, returns no row, and
# that empty result is the refusal — the store never asks "is it sealed?" separately and then acts.
APPEND = """
with numbered as (
    insert into call_log_head as head (log, agent, call, seq, started_at)
    values ($1, $2, $3, 1, $4::double precision)
    on conflict (log) do update
        set seq        = head.seq + 1,
            agent      = coalesce(head.agent, excluded.agent),
            call       = coalesce(head.call, excluded.call),
            started_at = coalesce(head.started_at, excluded.started_at)
        where not head.sealed
    returning seq
), written as (
    insert into call_log (call, seq, ts, agent, type, ephemeral, data)
    select $3, numbered.seq, $4::double precision, $2, $5, $6::boolean, $7::jsonb
    from numbered
    where not $6::boolean
)
select seq from numbered
"""

# The one thing a reader ever asks for: what is above my cursor, in order, at most this many.
PAGE = """
select call, seq, ts, agent, type, ephemeral, data
from call_log
where log = $1 and seq > $2
order by seq
limit $3
"""

# Sealing a log nobody wrote to is legal: a recovery path can reach the end before the beginning.
SEAL = """
insert into call_log_head (log, call, sealed) values ($1, $1, true)
on conflict (log) do update set sealed = true
"""

# The head row knows the call's whole life, so a call whose every entry was ephemeral is still
# listed. started_at is the appending process's clock, which is the only clock the entries have.
LIST_CALLS = """
select call from call_log_head
where agent = $1 and call is not null
order by started_at nulls last, log
"""

LATEST_SEQ = "select seq from call_log_head where log = $1"

# The org's calls across every agent, newest first: what the console's Sessions screen lists at
# the org level. The head row carries the org (0006) and the clock the entries have (started_at).
CALLS_OF = """
select call from call_log_head
where org = $1 and call is not null
order by started_at desc nulls last, log desc
limit $2
"""

# The head row is where a log's owner lives, and the first claim stands: a log is opened under one
# key and never moves. The row may not exist yet — a claim can land before the first entry — so
# it is inserted with a seq of 0, which is what APPEND's own insert would have written.
OWNED = """
insert into call_log_head as head (log, agent, call, org) values ($1, $2, $3, $4)
on conflict (log) do update set org = coalesce(head.org, excluded.org)
"""

OWNER = "select org from call_log_head where log = $1"

# The one read that spans every log: the metered types, by position, each with its log's owner.
# The partial index in 0006 is exactly this WHERE and ORDER BY.
ACROSS = """
select entry.position, head.org, entry.call, entry.seq, entry.ts, entry.agent, entry.type,
       entry.ephemeral, entry.data
from call_log entry
join call_log_head head on head.log = entry.log
where entry.type = any($1::text[]) and entry.position > $2
order by entry.position
limit $3
"""

# The two questions that span every log instead of asking about one. The Store protocol answers
# about ONE log, which is what keeps it portable, so these live on the Postgres store alone: only
# an operator asks them, and the CLI that does already holds this pool.
CALLS_NEWEST_FIRST = """
select call from call_log_head
where call is not null and ($2::text is null or agent = $2)
order by started_at desc nulls last, log desc
limit $1
"""

# Live means the log was never sealed: the call ended when somebody wrote its last entry.
NEWEST_LIVE_CALL = """
select call from call_log_head
where call is not null and not sealed
order by started_at desc nulls last, log desc
limit 1
"""

# The one health fact the doctor prints about a database, asked from the module that holds the
# driver so no CLI has to import one.
INSTALLED_EXTENSIONS = "select extname from pg_extension"


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


class PostgresStore:
    """A Store on one pool. Seq and ts are born in append(); ephemerals get a seq and no row."""

    def __init__(
        self,
        pool: Any,
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
            raise StoreUnreachable(f"{dsn}: {refused}") from refused
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
        seq: int | None = await self._pool.fetchval(
            APPEND, log_name(call, agent), agent, call, ts, type, ephemeral, data
        )
        if seq is None:
            raise LogSealed(f"call {call} has ended: {type} cannot be appended")
        return Entry(
            seq=seq, ts=ts, call=call, agent=agent, type=type, ephemeral=ephemeral, data=data
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
        rows: Sequence[Any] = await self._pool.fetch(LIST_CALLS, agent)
        return [str(row["call"]) for row in rows]

    async def calls_of(self, org: str, limit: int) -> list[str]:
        """The org's newest calls, off the head rows, across its agents."""
        rows: Sequence[Any] = await self._pool.fetch(CALLS_OF, org, limit)
        return [str(row["call"]) for row in rows]

    async def latest_seq(self, call: str) -> int:
        """The highest seq the log handed out, ephemerals counted; 0 for a call nobody wrote to."""
        seq: int | None = await self._pool.fetchval(LATEST_SEQ, call)
        return int(seq or 0)

    async def owned(self, call: str | None, agent: str, org: str) -> None:
        """Write the owner on the head row, creating it when the claim comes before any entry."""
        await self._pool.execute(OWNED, log_name(call, agent), agent, call, org)

    async def owner(self, call: str | None, agent: str) -> str | None:
        """Whose log this is; None for a log with no head row, or one nobody claimed."""
        org: str | None = await self._pool.fetchval(OWNER, log_name(call, agent))
        return None if org is None else str(org)

    async def across(
        self, types: Sequence[str], after: int = 0, limit: int = DEFAULT_LIMIT
    ) -> list[Metered]:
        """One page across every log: the database filters, orders and cuts, never us."""
        rows: Sequence[Any] = await self._pool.fetch(
            ACROSS, list(types), max(after, 0), max(limit, 0)
        )
        return [
            Metered(position=int(row["position"]), org=row["org"], entry=entry_of_row(row))
            for row in rows
        ]

    async def newest_calls(self, limit: int, agent: str | None = None) -> list[str]:
        """The newest calls, of one agent or of all: the database orders and cuts, never us."""
        rows: Sequence[Any] = await self._pool.fetch(CALLS_NEWEST_FIRST, limit, agent)
        return [str(row["call"]) for row in rows]

    async def newest_live_call(self) -> str | None:
        """The newest log nothing has sealed, or None when every call has ended."""
        rows: Sequence[Any] = await self._pool.fetch(NEWEST_LIVE_CALL)
        return str(rows[0]["call"]) if rows else None

    async def _page(self, log: str, after: int, limit: int) -> list[Entry]:
        """One page of one log, by the identity the database computes for every row."""
        rows: Sequence[Any] = await self._pool.fetch(PAGE, log, max(after, 0), max(limit, 0))
        return [entry_of_row(row) for row in rows]


def log_name(call: str | None, agent: str) -> str:
    """A log's identity, the same string the database generates: the call, or @ and the agent."""
    return call if call is not None else f"{AGENT_LOG_PREFIX}{agent}"


def entry_of_row(row: Any) -> Entry:
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
async def create_pool(dsn: str, *, schema: str = DEFAULT_SCHEMA) -> Any:
    """A plain connection pool, opened by the one module allowed to say the driver's name."""
    try:
        return await _create_pool(dsn, server_settings={"search_path": search_path_of(schema)})
    except (OSError, ValueError, asyncpg.PostgresError) as refused:
        # The same three the store's own connect turns into StoreUnreachable: a caller that opens
        # a pool must be able to say "no database answered" without naming the driver.
        raise StoreUnreachable(f"{dsn}: {refused}") from refused


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
