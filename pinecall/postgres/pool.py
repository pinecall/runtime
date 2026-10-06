"""The pool an instance opens on its database, and the names a schema and a DSN go by."""

import asyncio
import contextvars
import re
import weakref
from collections.abc import AsyncGenerator, Coroutine, Generator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool, PoolTimeout

from pinecall.domain.errors import DeclarationRefused, StoreUnreachable

type Connection = psycopg.AsyncConnection[DictRow]


type Pool = AsyncConnectionPool[Connection]


DEFAULT_SCHEMA = "public"


# A schema name also travels in the pool's libpq options string, where quoting does not reach.
_A_SCHEMA_NAME = re.compile(r"^[a-z_][a-z0-9_]*$")


CONNECT_TIMEOUT_S = 10


# What a gateway holds open to Postgres unless PINECALL_DB_POOL says otherwise: its writer's and
# its doors' together.
POOL_SIZE = 10


# SET LOCAL: they end with the transaction, and the connection goes back to the pool as it came.
UNBOUNDED = (
    "SET LOCAL statement_timeout = 0",
    "SET LOCAL idle_in_transaction_session_timeout = 0",
)


# Row-level security (migration 0096): a connection that names an org sees and writes that org's
# rows alone; one that names none is the box's, as the operator's doors and the loops are. The
# gateway names the org of a tenant's key for the whole request; a connection taken then says it.
SCOPED_ORG: ContextVar[str | None] = ContextVar("scoped_org", default=None)


SCOPE = "select set_config('pinecall.org', %s, false)"


@dataclass(frozen=True)
class Timeouts:
    """How long a pool's statement may run, its transaction sit idle, and a request wait for it."""

    # A door's statement past this is cancelled; what is long on purpose runs `unbounded`.
    statement_ms: int = 30_000
    # A transaction its process stopped talking in is ended, and its locks let go.
    idle_in_transaction_ms: int = 60_000
    # A request that finds the pool full this long is answered 503, which a worker outlasts.
    wait_s: float = 2.0


TIMEOUTS = Timeouts()


# What each connection was last told, so a connection that already says the org is asked nothing:
# a request's doors share a few connections, and the box's loops never name one.
_SAYS: weakref.WeakKeyDictionary[Connection, str] = weakref.WeakKeyDictionary()


async def open_pool(
    dsn: str,
    *,
    schema: str = DEFAULT_SCHEMA,
    max_size: int = POOL_SIZE,
    min_size: int = 1,
    timeouts: Timeouts = TIMEOUTS,
) -> Pool:
    """Open a pool on the schema, rows as dicts; raise StoreUnreachable when nothing answers."""
    options = (
        f"-c search_path={','.join(schemas_of(schema))}"
        f" -c statement_timeout={timeouts.statement_ms}"
        f" -c idle_in_transaction_session_timeout={timeouts.idle_in_transaction_ms}"
    )
    # A DSN's own options (a role to act as, say) are kept, and the pool's follow them.
    given = conninfo_to_dict(dsn).get("options")
    pool: Pool = AsyncConnectionPool(
        dsn,
        connection_class=psycopg.AsyncConnection[DictRow],
        # A statement alone is one round trip; what belongs together opens connection.transaction().
        kwargs={
            "autocommit": True,
            "row_factory": dict_row,
            "options": options if not given else f"{given} {options}",
        },
        min_size=min_size,
        max_size=max_size,
        timeout=timeouts.wait_s,
        open=False,
        check=_scoped,
    )
    try:
        await pool.open(wait=True, timeout=CONNECT_TIMEOUT_S)
    except (PoolTimeout, psycopg.OperationalError) as refused:
        await pool.close()
        raise StoreUnreachable(f"{database_named(dsn)}: {refused}") from refused
    return pool


# Erasure, an export, the nightly retention and a knowledge base's index are long on purpose.
@asynccontextmanager
async def unbounded(pool: Pool) -> AsyncGenerator[Connection]:
    """A connection in a transaction that no statement or idle timeout of the pool ends."""
    async with pool.connection() as connection, connection.transaction():
        for statement in UNBOUNDED:
            await connection.execute(statement)
        yield connection


def scope_to(org: str) -> None:
    """Name the org every connection taken from here on in this request acts for."""
    SCOPED_ORG.set(org)


# What a tenant's request reads across orgs on purpose (is this address in another org, does
# another org hold this number) is read box-wide, in a block that says so.
@contextmanager
def box_wide() -> Generator[None]:
    """Take connections as the box's within the block, whatever the request's org."""
    token = SCOPED_ORG.set(None)
    try:
        yield
    finally:
        SCOPED_ORG.reset(token)


# A task of the process (the log's writer, the relay, a cache's listener) may start inside a
# tenant's request: it starts box-wide, or it would act for that org for the rest of its life.
def box_task[T](work: Coroutine[object, object, T]) -> asyncio.Task[T]:
    """Start the work as a task of the box's, whatever org the request starting it acts for."""
    context = contextvars.copy_context()
    context.run(SCOPED_ORG.set, None)
    return asyncio.create_task(work, context=context)


async def connect(dsn: str) -> Connection:
    """Open one autocommit connection, rows as dicts; the caller closes it."""
    return await psycopg.AsyncConnection[DictRow].connect(
        dsn, autocommit=True, row_factory=dict_row, connect_timeout=CONNECT_TIMEOUT_S
    )


# Extension types and operators (halfvec, <=>, bm25's <@>) live in public, so another schema
# keeps public on its search path.
def schemas_of(schema: str) -> tuple[str, ...]:
    """Return the search path for a schema: the schema, then public."""
    name = check_schema_name(schema)
    return (name,) if name == DEFAULT_SCHEMA else (name, DEFAULT_SCHEMA)


def check_schema_name(schema: str) -> str:
    """Return the schema name, raising DeclarationRefused unless it is a plain lowercase word."""
    if not _A_SCHEMA_NAME.match(schema):
        raise DeclarationRefused(f"a schema name is a lowercase word, not {schema!r}")
    return schema


# Every message that names the database goes through this: a DSN carries the password.
def database_named(dsn: str) -> str:
    """Return `host/database` from the DSN, with no credentials."""
    parts = conninfo_to_dict(dsn)
    return f"{parts.get('host', 'localhost')}/{parts.get('dbname', '')}"


async def _scoped(connection: Connection) -> None:
    wanted = SCOPED_ORG.get() or ""
    if _SAYS.get(connection, "") != wanted:
        await connection.execute(SCOPE, (wanted,))
        _SAYS[connection] = wanted
