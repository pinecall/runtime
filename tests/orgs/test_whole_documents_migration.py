"""0038 on a box with chunks: every row is `retrieved`, and the vector may be empty from here."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, cast
from uuid import uuid4

import asyncpg  # type: ignore[import-untyped]  # pyright: ignore[reportMissingTypeStubs]
import pytest

from pinecall.log.store.migrating import (
    MIGRATIONS_TABLE,
    RECORD_MIGRATION,
    a_hash,
    apply_migrations,
)
from pinecall.log.store.postgres import MIGRATIONS, search_path_of
from pinecall.providers.embedder import DIMENSIONS
from tests.postgres import Dev

pytestmark = pytest.mark.postgres

BEFORE_WHOLE = tuple(path.name for path in sorted(MIGRATIONS.glob("*.sql")) if path.name < "0038")
THE_ORG = "clinica"

_connect = cast("Any", asyncpg.connect)  # pyright: ignore[reportUnknownMemberType]


@dataclass(frozen=True)
class Box:
    """A database as a box had it before 0038, and the connection this test reads it through."""

    dsn: str
    schema: str
    connection: Any


@pytest.fixture
async def a_box_from_before(postgres: Dev) -> AsyncIterator[Box]:
    """Every migration up to 0037 applied by hand, one org, one base with one chunk."""
    schema = f"pinecall_before_whole_{uuid4().hex[:12]}"
    connection = await _connect(postgres.dsn)
    try:
        await connection.execute(f"create schema {schema}")
        await connection.execute(f"set search_path to {search_path_of(schema)}")
        await connection.execute(MIGRATIONS_TABLE)
        for name in BEFORE_WHOLE:
            await connection.execute((MIGRATIONS / name).read_text(encoding="utf-8"))
            await connection.execute(RECORD_MIGRATION, name, a_hash(MIGRATIONS / name))
        await connection.execute("insert into orgs (id, slug, name) values ($1, $1, $1)", THE_ORG)
        await connection.execute(
            "insert into knowledge_bases (org, env, holder, base, model, dimensions, chunks) "
            "values ($1, 'production', '', 'clinica', 'm', $2, 1)",
            THE_ORG,
            DIMENSIONS,
        )
        await connection.execute(
            "insert into knowledge_chunks (org, env, holder, base, path, heading, ordinal, text, "
            "embedding) values ($1, 'production', '', 'clinica', 'a.md', null, 0, 'x', "
            "$2::halfvec)",
            THE_ORG,
            "[" + ",".join(["0.5"] * DIMENSIONS) + "]",
        )
        yield Box(dsn=postgres.dsn, schema=schema, connection=connection)
    finally:
        await connection.execute(f"drop schema if exists {schema} cascade")
        await connection.close()


async def test_0038_marks_every_chunk_retrieved_and_lets_a_whole_row_carry_no_vector(
    a_box_from_before: Box,
) -> None:
    box = a_box_from_before
    applied = (await apply_migrations(box.dsn, schema=box.schema)).applied
    assert applied[0] == "0038_whole_documents.sql"
    assert await box.connection.fetchval("select mode from knowledge_chunks") == "retrieved"
    await box.connection.execute(
        "insert into knowledge_chunks (org, env, holder, base, path, heading, ordinal, text, "
        "embedding, mode) values ($1, 'production', '', 'clinica', 'b.md', null, 0, 'y', null, "
        "'whole')",
        THE_ORG,
    )
    with pytest.raises(asyncpg.CheckViolationError):
        await box.connection.execute(
            "insert into knowledge_chunks (org, env, holder, base, path, heading, ordinal, text, "
            "embedding, mode) values ($1, 'production', '', 'clinica', 'c.md', null, 0, 'z', null, "
            "'verbatim')",
            THE_ORG,
        )
