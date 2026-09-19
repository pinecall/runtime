"""A whole file in Postgres: one bare row, never searched, read for the block; promote copies."""

from collections.abc import AsyncIterator
from typing import Any

import pytest

from pinecall.knowledge import PgKnowledge
from pinecall.log.store import open_pool
from pinecall.types import PRODUCTION, SANDBOX, KnowledgeFile
from tests.knowledge.files import CLINICA, TARIFAS, an_org
from tests.postgres import Dev
from tests.vectors import HashEmbedder

pytestmark = pytest.mark.postgres

THE_BASE = "clinica"
BY_HEART = KnowledgeFile("maravilla.md", "# Maravilla\n\nAbrimos a las nueve.\n", "whole")


@pytest.fixture
async def knowledge(postgres: Dev) -> AsyncIterator[PgKnowledge]:
    pool = await open_pool(postgres.dsn, schema=postgres.schema)
    try:
        yield PgKnowledge(pool, HashEmbedder())
    finally:
        await pool.close()


@pytest.fixture
async def org(raw_connection: Any) -> str:
    return await an_org(raw_connection)


async def test_a_whole_file_is_one_row_with_no_vector_that_no_search_answers(
    knowledge: PgKnowledge, org: str, raw_connection: Any
) -> None:
    assert await knowledge.put(org, PRODUCTION, None, THE_BASE, [CLINICA, BY_HEART]) == 3
    row = await raw_connection.fetchrow(
        "select mode, embedding is null as bare, text from knowledge_chunks "
        "where org = $1 and path = 'maravilla.md'",
        org,
    )
    assert (row["mode"], row["bare"], row["text"]) == ("whole", True, BY_HEART.text)
    found = await knowledge.search(org, PRODUCTION, None, THE_BASE, "abrimos nueve")
    assert "maravilla.md" not in {chunk.path for chunk in found}
    assert await knowledge.whole_texts(org, PRODUCTION, None, THE_BASE) == [BY_HEART]


async def test_the_whole_files_fall_back_to_the_orgs_own_as_a_search_does(
    knowledge: PgKnowledge, org: str
) -> None:
    await knowledge.put(org, SANDBOX, None, THE_BASE, [BY_HEART])
    assert await knowledge.whole_texts(org, SANDBOX, "m_ana", THE_BASE) == [BY_HEART]
    await knowledge.put(org, SANDBOX, "m_ana", THE_BASE, [TARIFAS])
    assert await knowledge.whole_texts(org, SANDBOX, "m_ana", THE_BASE) == []


async def test_promoting_copies_the_rows_and_their_vectors_into_the_other_world(
    knowledge: PgKnowledge, org: str, raw_connection: Any
) -> None:
    await knowledge.put(org, SANDBOX, None, THE_BASE, [CLINICA, BY_HEART])
    await knowledge.put(org, PRODUCTION, None, THE_BASE, [TARIFAS])
    assert await knowledge.copy(org, SANDBOX, None, THE_BASE, PRODUCTION) == 3
    [listed] = await knowledge.bases(org, PRODUCTION)
    assert listed.chunks == 3
    assert await knowledge.whole_texts(org, PRODUCTION, None, THE_BASE) == [BY_HEART]
    found = await knowledge.search(org, PRODUCTION, None, THE_BASE, "turnos teléfono")
    assert {chunk.path for chunk in found} == {"clinica.md"}
    embedded = await raw_connection.fetchval(
        "select count(*) from knowledge_chunks "
        "where org = $1 and env = $2 and embedding is not null",
        org,
        PRODUCTION,
    )
    assert embedded == 2


async def test_promoting_a_developers_corner_copies_the_orgs_own_when_they_pushed_none(
    knowledge: PgKnowledge, org: str
) -> None:
    await knowledge.put(org, SANDBOX, None, THE_BASE, [BY_HEART])
    assert await knowledge.copy(org, SANDBOX, "m_ana", THE_BASE, PRODUCTION) == 1
    assert await knowledge.copy(org, SANDBOX, None, "nunca", PRODUCTION) == 0
