"""0021 against a box that HAS rows: the backfill, the rebuilt key, and nothing lost."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from tests.support.migrations import Before, a_box_before
from tests.support.postgres import Dev

pytestmark = pytest.mark.postgres

THE_MIGRATION = "0021_a_developers_own.sql"

ORG = "org_a_box_in_use"

# The width the tables were made at (0008, 0009): a halfvec column takes this many and no other.
DIMENSIONS = 1024
A_VECTOR = "[" + ",".join(["0"] * DIMENSIONS) + "]"
CONTACT = "+59899111111"
A_BASE = "clinica"


@pytest.fixture
async def before(postgres: Dev) -> AsyncIterator[Before]:
    """The schema as it stood at 0020, with a tenant, a base, chunks and a contact's facts."""
    async for box in a_box_before(postgres, THE_MIGRATION):
        write = box.connection
        await write.execute("insert into orgs (id, slug, name) values ($1, $1, $1)", ORG)
        await write.execute(
            """insert into knowledge_bases (org, env, base, model, dimensions, chunks, pushed_at)
               values ($1, 'production', $2, 'a-model', $3, 1, now())""",
            ORG,
            A_BASE,
            DIMENSIONS,
        )
        await write.execute(
            """insert into knowledge_chunks
                   (org, env, base, path, heading, ordinal, text, embedding)
               values ($1, 'production', $2, 'tarifas.md', 'Tarifas', 0, 'la revisión sale 1200',
                       $3::text::halfvec)""",
            ORG,
            A_BASE,
            A_VECTOR,
        )
        await write.execute(
            """insert into contact_memories
                   (org, env, contact, text, category, embedding, valid_from, model)
               values ($1, 'production', $2, 'prefiere la tarde', 'preference',
                       $3::text::halfvec, now(), 'a-model')""",
            ORG,
            CONTACT,
            A_VECTOR,
        )
        yield box


async def test_it_applies_at_all_against_a_populated_schema(before: Before) -> None:
    """A `NOT NULL` on a populated column and a primary key rebuilt under rows: the two ways a
    migration passes on an empty database and takes a box down on a full one."""
    assert THE_MIGRATION in await before.take_it()


async def test_every_row_that_was_there_is_still_there(before: Before) -> None:
    await before.take_it()

    bases = await before.rows("select org, base, holder from knowledge_bases")
    chunks = await before.rows("select text, holder from knowledge_chunks")
    facts = await before.rows("select text, holder from contact_memories")

    assert [(row["org"], row["base"]) for row in bases] == [(ORG, A_BASE)]
    assert [row["text"] for row in chunks] == ["la revisión sale 1200"]
    assert [row["text"] for row in facts] == ["prefiere la tarde"]


async def test_the_backfill_makes_everything_the_orgs_own(before: Before) -> None:
    """Production is always the org's, and so is anything a development key naming nobody wrote,
    which is why the default is right for every row that already existed."""
    await before.take_it()

    for table in ("knowledge_bases", "knowledge_chunks", "contact_memories"):
        holders = await before.rows(f"select distinct holder from {table}")
        assert [row["holder"] for row in holders] == [""], table


async def test_the_chunks_still_go_with_their_base(before: Before) -> None:
    """The foreign key was dropped and rebuilt around the new column: it has to still cascade."""
    await before.take_it()

    await before.connection.execute(
        "delete from knowledge_bases where org = $1 and base = $2", ORG, A_BASE
    )

    assert await before.rows("select 1 from knowledge_chunks") == []


async def test_the_column_carries_no_default_afterwards(before: Before) -> None:
    """No policy lives in the table: from here every writer says whose a row is."""
    await before.take_it()

    defaults = await before.rows(
        """select table_name, column_default from information_schema.columns
           where column_name = 'holder' and table_schema = current_schema()"""
    )

    assert defaults, "the column exists on at least one table"
    assert all(row["column_default"] is None for row in defaults)
