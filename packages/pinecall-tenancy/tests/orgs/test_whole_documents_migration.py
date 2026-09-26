"""0038 on a box with chunks: every row is `retrieved`, and the vector may be empty from here."""

from collections.abc import AsyncIterator

import asyncpg  # type: ignore[import-untyped]  # pyright: ignore[reportMissingTypeStubs]
import pytest

from pinecall.db import apply_migrations
from pinecall.providers.embedder import DIMENSIONS
from pinecall_testkit.postgres import Dev
from tests.orgs.boxes import Box, a_box_before

pytestmark = pytest.mark.postgres

THE_ORG = "clinica"


@pytest.fixture
async def a_box_from_before(postgres: Dev) -> AsyncIterator[Box]:
    """Every migration up to 0037 applied by hand, one org, one base with one chunk."""
    async with a_box_before(postgres, "0038", named="whole") as box:
        await box.connection.execute(
            "insert into orgs (id, slug, name) values ($1, $1, $1)", THE_ORG
        )
        await box.connection.execute(
            "insert into knowledge_bases (org, env, holder, base, model, dimensions, chunks) "
            "values ($1, 'production', '', 'clinica', 'm', $2, 1)",
            THE_ORG,
            DIMENSIONS,
        )
        await box.connection.execute(
            "insert into knowledge_chunks (org, env, holder, base, path, heading, ordinal, text, "
            "embedding) values ($1, 'production', '', 'clinica', 'a.md', null, 0, 'x', "
            "$2::halfvec)",
            THE_ORG,
            "[" + ",".join(["0.5"] * DIMENSIONS) + "]",
        )
        yield box


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
