"""Tests for the pool, its timeouts, the schema names and what a DSN is called in a message."""

import asyncio

import pytest
from psycopg import errors
from psycopg_pool import PoolTimeout

from pinecall.domain.errors import DeclarationRefused, StoreUnreachable
from pinecall.postgres.pool import (
    POOL_SIZE,
    TIMEOUTS,
    Pool,
    Timeouts,
    check_schema_name,
    database_named,
    open_pool,
    schemas_of,
    unbounded,
)
from tests.conftest import DSN, HELD_PAST_THE_TIMEOUT_S, postgres


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


# ── timeouts ──


def test_a_statement_is_cut_at_30_s_an_idle_transaction_at_60_s_and_a_wait_at_2_s() -> None:
    assert Timeouts(statement_ms=30_000, idle_in_transaction_ms=60_000, wait_s=2.0) == TIMEOUTS
    assert POOL_SIZE == 10


@postgres
async def test_every_connection_of_the_pool_carries_its_timeouts() -> None:
    pool = await open_pool(DSN)
    try:
        async with pool.connection() as connection:
            statement = await (await connection.execute("show statement_timeout")).fetchone()
            idle = await (
                await connection.execute("show idle_in_transaction_session_timeout")
            ).fetchone()
    finally:
        await pool.close()
    assert statement == {"statement_timeout": "30s"}
    assert idle == {"idle_in_transaction_session_timeout": "1min"}


@postgres
async def test_a_statement_past_the_timeout_is_cancelled(impatient_pool: Pool) -> None:
    async with impatient_pool.connection() as connection:
        with pytest.raises(errors.QueryCanceled, match="statement timeout"):
            await connection.execute("select pg_sleep(%s)", (HELD_PAST_THE_TIMEOUT_S,))


@postgres
async def test_an_unbounded_transaction_runs_past_it_and_the_connection_comes_back_bounded(
    impatient_pool: Pool,
) -> None:
    async with unbounded(impatient_pool) as connection:
        await connection.execute("select pg_sleep(%s)", (HELD_PAST_THE_TIMEOUT_S,))
        timeout = await (await connection.execute("show statement_timeout")).fetchone()
    assert timeout == {"statement_timeout": "0"}
    for _ in range(2):
        async with impatient_pool.connection() as connection:
            again = await (await connection.execute("show statement_timeout")).fetchone()
        assert again == {"statement_timeout": "100ms"}


@postgres
async def test_a_transaction_left_idle_past_its_timeout_is_ended(impatient_pool: Pool) -> None:
    # A pooled connection is not in autocommit: its first statement opens the transaction.
    async with impatient_pool.connection() as connection:
        await connection.execute("select 1")
        await asyncio.sleep(HELD_PAST_THE_TIMEOUT_S)
        with pytest.raises(errors.IdleInTransactionSessionTimeout):
            await connection.execute("select 1")


@postgres
async def test_an_unbounded_transaction_may_sit_idle(impatient_pool: Pool) -> None:
    async with unbounded(impatient_pool) as connection:
        await asyncio.sleep(HELD_PAST_THE_TIMEOUT_S)
        row = await (await connection.execute("select 1 as one")).fetchone()
    assert row == {"one": 1}


@postgres
async def test_a_request_that_finds_the_pool_full_is_refused_after_the_wait() -> None:
    pool = await open_pool(DSN, max_size=1, timeouts=Timeouts(wait_s=0.1))
    try:
        async with pool.connection():
            with pytest.raises(PoolTimeout):
                async with pool.connection():
                    pass
    finally:
        await pool.close()
