"""Tests for the pool, the schema names and what a DSN is called in a message."""

import os

import pytest

from pinecall.domain.errors import DeclarationRefused, StoreUnreachable
from pinecall.postgres.pool import check_schema_name, database_named, open_pool, schemas_of

DSN = os.environ.get("DATABASE_URL", "")

postgres = pytest.mark.skipif(not DSN, reason="DATABASE_URL: a Postgres, `make test`")


def test_a_schema_name_is_a_lowercase_word_and_public_stays_on_the_path() -> None:
    assert check_schema_name("pinecall_test") == "pinecall_test"
    assert schemas_of("public") == ("public",)
    assert schemas_of("pinecall_test") == ("pinecall_test", "public")
    for spelled in ("", "Pinecall", "a b", "a;drop", "1abc"):
        with pytest.raises(DeclarationRefused, match="lowercase word"):
            check_schema_name(spelled)


def test_the_database_is_named_without_its_password() -> None:
    assert database_named("postgresql://user:s3cret@db.example.test:5432/pinecall") == (
        "db.example.test/pinecall"
    )
    assert "s3cret" not in database_named("postgresql://user:s3cret@[::1]/pinecall")


@postgres
async def test_a_pool_opens_on_the_sandbox_and_answers_a_query() -> None:
    pool = await open_pool(DSN)
    try:
        async with pool.connection() as connection:
            row = await (await connection.execute("select 1 as one")).fetchone()
    finally:
        await pool.close()
    assert row == {"one": 1}


@postgres
async def test_a_pool_that_reaches_nothing_says_so_without_the_password() -> None:
    nowhere = "postgresql://nobody:s3cret@127.0.0.1:1/none?connect_timeout=1"
    with pytest.raises(StoreUnreachable) as refused:
        await open_pool(nowhere)
    assert "127.0.0.1/none" in str(refused.value)
    assert "s3cret" not in str(refused.value)
