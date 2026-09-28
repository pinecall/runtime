"""The migration runner: the .sql files beside it, applied in order, never edited."""

import asyncio
import hashlib
from dataclasses import dataclass
from pathlib import Path

import psycopg
from psycopg import sql

from pinecall.domain.errors import MigrationsRefused
from pinecall.postgres.pool import (
    DEFAULT_SCHEMA,
    Connection,
    Pool,
    connect,
    database_named,
    schemas_of,
)

# Applied in name order. An applied migration is never edited: add a new one instead.
MIGRATIONS = Path(__file__).parent / "migrations"
FIRST = "0001_schema.sql"

# First-line marker that runs a file outside a transaction, required by CREATE INDEX CONCURRENTLY.
# Such a file holds ONE statement: Postgres runs a multi-statement string as one implicit
# transaction.
NO_TRANSACTION = "-- pinecall:no-transaction"

# Fails fast instead of queueing behind a reader and blocking every writer behind it.
LOCK_TIMEOUT_MS = 1_000

# Serializes concurrent runners; any constant no other code in the database uses.
ADVISORY_LOCK = 0x9E3_C411

MIGRATIONS_TABLE = """
create table if not exists schema_migrations (
    name       text primary key,
    applied_at timestamptz not null default now(),
    sha256     text
)
"""
APPLIED = "select name, sha256 from schema_migrations order by name"
RECORD = "insert into schema_migrations (name, sha256) values (%s, %s)"

EDITED = (
    "migration {name} changed after it was applied: this database ran a different file.\n"
    "  applied  sha256 {was}\n"
    "  on disk  sha256 {now}\n"
    "An applied migration is never edited: every database that ran it has the OLD one. Put the "
    "change in a new migration, or restore the file."
)
MISSING = (
    "migration {name} is recorded as applied and is not in this distribution: "
    "this checkout is older than the database it is pointed at"
)


@dataclass(frozen=True)
class Applied:
    """The result of a migration run: the database, the schema and what was applied."""

    database: str
    schema: str
    applied: tuple[str, ...]


async def apply_migrations(dsn: str, *, schema: str = DEFAULT_SCHEMA) -> Applied:
    """Apply every pending migration in name order under an advisory lock."""
    schemas = schemas_of(schema)
    async with await connect(dsn) as connection:
        # Session-level, released with the connection. Taken before any DDL: concurrent
        # `create schema if not exists` can still fail on pg_namespace's unique index.
        await connection.execute("select pg_advisory_lock(%s)", (ADVISORY_LOCK,))
        if schema != DEFAULT_SCHEMA:
            await connection.execute(
                sql.SQL("create schema if not exists {}").format(sql.Identifier(schema))
            )
        await connection.execute(
            sql.SQL("set search_path to {}").format(
                sql.SQL(", ").join(sql.Identifier(schema) for schema in schemas)
            )
        )
        await connection.execute(MIGRATIONS_TABLE)
        done = await _applied(connection)
        ran = [
            await _apply_one(connection, path)
            for path in migration_files()
            if path.name not in done
        ]
    return Applied(database=database_named(dsn), schema=schema, applied=tuple(ran))


async def migrations_behind(pool: Pool) -> tuple[str, ...]:
    """Return the migrations the database has not applied; every one, when it has no table."""
    async with pool.connection() as connection:
        try:
            rows = await (await connection.execute(APPLIED)).fetchall()
        except psycopg.errors.UndefinedTable:
            rows = []
    done = {str(row["name"]) for row in rows}
    return tuple(path.name for path in migration_files() if path.name not in done)


def migration_files() -> list[Path]:
    """Return every shipped migration, in apply order."""
    return sorted(MIGRATIONS.glob("*.sql"))


def file_hash(path: Path) -> str:
    """Return the SHA-256 of the file's bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def in_a_transaction(statements: bytes) -> bool:
    """Return False when the file's first line is the no-transaction marker."""
    first = statements.decode("utf-8").lstrip().split("\n", 1)[0].strip()
    return first != NO_TRANSACTION


async def _applied(connection: Connection) -> set[str]:
    on_disk = {path.name: path for path in migration_files()}
    rows = await (await connection.execute(APPLIED)).fetchall()
    recorded = {str(row["name"]): row["sha256"] for row in rows}
    # A database the previous runtime migrated: its schema is what the first file holds, so the
    # first file is recorded as applied and the old bookkeeping goes.
    if recorded and FIRST not in recorded:
        await connection.execute("delete from schema_migrations")
        await connection.execute(RECORD, (FIRST, file_hash(on_disk[FIRST])))
        return {FIRST}
    for name, was in recorded.items():
        path = on_disk.get(name)
        if path is None:
            raise MigrationsRefused(MISSING.format(name=name))
        now = file_hash(path)
        if str(was) != now:
            raise MigrationsRefused(EDITED.format(name=name, was=was, now=now))
    return set(recorded)


async def _apply_one(connection: Connection, path: Path) -> str:
    statements = await asyncio.to_thread(path.read_bytes)
    recorded = (path.name, hashlib.sha256(statements).hexdigest())
    if not in_a_transaction(statements):
        await connection.execute(statements)
        await connection.execute(RECORD, recorded)
        return path.name
    async with connection.transaction():
        await connection.execute(
            sql.SQL("set local lock_timeout = {}").format(sql.Literal(LOCK_TIMEOUT_MS))
        )
        await connection.execute(statements)
        await connection.execute(RECORD, recorded)
    return path.name
