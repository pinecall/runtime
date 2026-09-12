"""A database as a box HAD it, so a migration is tried against rows and not against nothing."""

# Every other suite builds a schema by applying every migration to an empty one, which proves that
# a migration parses and nothing else. The migrations that break a box are the ones that meet
# DATA: a backfill, a `NOT NULL` on a populated column, a primary key rebuilt under rows that must
# survive it. 0021 was all three, and nothing in this tree would have caught it being wrong.
#
# So: apply everything BEFORE the one under test, write the rows a real box would have, then apply
# it. A test imports `a_box_before` and wraps it in a fixture of its own, because the rows a
# migration has to survive are that migration's business and nobody else's.

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, cast
from uuid import uuid4

import asyncpg  # type: ignore[import-untyped]  # pyright: ignore[reportMissingTypeStubs]

from pinecall.log.store.migrating import (
    MIGRATIONS_TABLE,
    RECORD_MIGRATION,
    a_hash,
    apply_migrations,
    every,
)
from pinecall.log.store.postgres import MIGRATIONS, search_path_of
from tests.postgres import Dev

_connect = cast("Any", asyncpg.connect)  # pyright: ignore[reportUnknownMemberType]


@dataclass(frozen=True)
class Before:
    """A schema as it stood the day before one migration, and the connection to write it with."""

    dsn: str
    schema: str
    connection: Any

    async def take_it(self) -> tuple[str, ...]:
        """Apply from here on, and answer what ran. The one call the test is actually about."""
        applied = await apply_migrations(self.dsn, schema=self.schema)
        return applied.applied

    async def rows(self, query: str) -> list[Any]:
        """What is in the table now, read through this schema."""
        return list(await self.connection.fetch(query))


async def a_box_before(postgres: Dev, migration: str) -> AsyncIterator[Before]:
    """Every migration up to but not including `migration`, in a schema of this test's own.

    The rows are the test's to write: what a box would have had is what the migration has to
    survive, and only the test knows which of them matter.
    """
    names = [path.name for path in every()]
    if migration not in names:
        raise AssertionError(f"no such migration: {migration}")
    schema = f"pinecall_before_{uuid4().hex[:12]}"
    connection = await _connect(postgres.dsn)
    try:
        await connection.execute(f"create schema {schema}")
        await connection.execute(f"set search_path to {search_path_of(schema)}")
        await connection.execute(MIGRATIONS_TABLE)
        for name in names[: names.index(migration)]:
            path = MIGRATIONS / name
            await connection.execute(path.read_text(encoding="utf-8"))
            await connection.execute(RECORD_MIGRATION, name, a_hash(path))
        yield Before(dsn=postgres.dsn, schema=schema, connection=connection)
    finally:
        await connection.execute(f"drop schema if exists {schema} cascade")
        await connection.close()
