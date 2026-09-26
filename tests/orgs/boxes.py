"""A database as a box had it before one migration: built by hand in a schema of its own."""

from collections.abc import AsyncGenerator, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, cast
from uuid import uuid4

import asyncpg  # type: ignore[import-untyped]  # pyright: ignore[reportMissingTypeStubs]

from pinecall.log.store.migrating import MIGRATIONS_TABLE, RECORD_MIGRATION, a_hash
from pinecall.log.store.postgres import MIGRATIONS, search_path_of
from tests.postgres import Dev

_connect = cast("Any", asyncpg.connect)  # pyright: ignore[reportUnknownMemberType]


@dataclass(frozen=True)
class Box:
    """A database as a box had it before a migration, and the connection a test reads it through."""

    dsn: str
    schema: str
    connection: Any


def migrations_before(number: str) -> tuple[str, ...]:
    """Every migration below this number, by name, in the order a running box applied them."""
    return tuple(path.name for path in sorted(MIGRATIONS.glob("*.sql")) if path.name < number)


async def applied_by_hand(connection: Any, names: Iterable[str]) -> None:
    """Run these migrations and record them as `migrate up` would, without `migrate up`."""
    for name in names:
        await connection.execute((MIGRATIONS / name).read_text(encoding="utf-8"))
        await connection.execute(RECORD_MIGRATION, name, a_hash(MIGRATIONS / name))


@asynccontextmanager
async def a_box_before(postgres: Dev, migration: str, *, named: str) -> AsyncGenerator[Box]:
    """A schema of its own, every migration below this one applied by hand, dropped at the end."""
    schema = f"pinecall_before_{named}_{uuid4().hex[:12]}"
    connection = await _connect(postgres.dsn)
    try:
        await connection.execute(f"create schema {schema}")
        await connection.execute(f"set search_path to {search_path_of(schema)}")
        await connection.execute(MIGRATIONS_TABLE)
        await applied_by_hand(connection, migrations_before(migration))
        yield Box(dsn=postgres.dsn, schema=schema, connection=connection)
    finally:
        await connection.execute(f"drop schema if exists {schema} cascade")
        await connection.close()
