"""The numbered SQL: running what a database has not run, and saying what it has not run."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import asyncpg  # type: ignore[import-untyped]  # pyright: ignore[reportMissingTypeStubs]

from pinecall.log.store.postgres import (
    DEFAULT_SCHEMA,
    MIGRATIONS,
    a_schema_name,
    connect,
    search_path_of,
)

# Applied once and never edited, so the record is a NAME and not a checksum: a migration that
# changed after it ran is a migration two databases disagree about, and the rule against
# editing one is what makes the name enough.
APPLIED_MIGRATIONS = "select name from schema_migrations"

RECORD_MIGRATION = "insert into schema_migrations (name) values ($1)"

MIGRATIONS_TABLE = """
create table if not exists schema_migrations (
    name       text primary key,
    applied_at timestamptz not null default now()
)
"""


async def apply_migrations(dsn: str, *, schema: str = DEFAULT_SCHEMA) -> list[str]:
    """Run every .sql this database has not run yet, in name order. Returns what it applied."""
    name = a_schema_name(schema)
    connection: Any = await connect(dsn)
    try:
        if name != DEFAULT_SCHEMA:
            await connection.execute(f"create schema if not exists {name}")
        await connection.execute(f"set search_path to {search_path_of(name)}")
        await connection.execute(MIGRATIONS_TABLE)
        done = {str(row["name"]) for row in await connection.fetch(APPLIED_MIGRATIONS)}
        return [
            await _apply_one(connection, path)
            for path in sorted(MIGRATIONS.glob("*.sql"))
            if path.name not in done
        ]
    finally:
        await connection.close()


# The same question `apply_migrations` answers by doing it, asked without doing it: what a gateway
# checks at startup, because a process whose schema is behind does not fail at startup — it fails
# one door at a time, per request, as an UndefinedColumnError three frames deep. On a box nothing
# can be behind (the unit runs `migrate up` before every start); on a laptop nothing runs it for
# you, and this is the line that says so.
async def migrations_behind(pool: Any) -> tuple[str, ...]:
    """Every migration this database has not run, in name order. Empty is a schema that is level."""
    done: set[str] = set()
    try:
        rows: Sequence[Any] = await pool.fetch(APPLIED_MIGRATIONS)
        done = {str(row["name"]) for row in rows}
    except asyncpg.PostgresError:
        # No table of its own yet: nothing has ever been applied, so everything is behind.
        pass
    return tuple(path.name for path in sorted(MIGRATIONS.glob("*.sql")) if path.name not in done)


async def _apply_one(connection: Any, path: Path) -> str:
    """One migration and its record in one transaction: half a migration is never recorded."""
    async with connection.transaction():
        await connection.execute(path.read_text(encoding="utf-8"))
        await connection.execute(RECORD_MIGRATION, path.name)
    return path.name
