"""The pool an instance opens on its database, and the names a schema and a DSN go by."""

import re

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool, PoolTimeout

from pinecall.domain.errors import DeclarationRefused, StoreUnreachable

DEFAULT_SCHEMA = "public"

# A schema name also travels in the pool's libpq options string, where quoting does not reach.
_A_SCHEMA_NAME = re.compile(r"^[a-z_][a-z0-9_]*$")

CONNECT_TIMEOUT_S = 10

type Connection = psycopg.AsyncConnection[DictRow]
type Pool = AsyncConnectionPool[Connection]


async def open_pool(dsn: str, *, schema: str = DEFAULT_SCHEMA, max_size: int = 10) -> Pool:
    """Open a pool on the schema, rows as dicts; raise StoreUnreachable when nothing answers."""
    pool: Pool = AsyncConnectionPool(
        dsn,
        connection_class=psycopg.AsyncConnection[DictRow],
        kwargs={
            "row_factory": dict_row,
            "options": f"-c search_path={','.join(schemas_of(schema))}",
        },
        min_size=1,
        max_size=max_size,
        open=False,
    )
    try:
        await pool.open(wait=True, timeout=CONNECT_TIMEOUT_S)
    except (PoolTimeout, psycopg.OperationalError) as refused:
        await pool.close()
        raise StoreUnreachable(f"{database_named(dsn)}: {refused}") from refused
    return pool


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
