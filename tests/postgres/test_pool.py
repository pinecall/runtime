"""Tests for the pool, its timeouts, the schema names and what a DSN is called in a message."""

import asyncio
import contextvars
from unittest.mock import patch

import pytest
from psycopg import AsyncConnection, errors
from psycopg.abc import Params, QueryNoTemplate
from psycopg.pq import TransactionStatus
from psycopg.rows import DictRow
from psycopg_pool import PoolTimeout

from pinecall.domain.errors import DeclarationRefused, StoreUnreachable
from pinecall.postgres.pool import (
    POOL_SIZE,
    TIMEOUTS,
    Pool,
    Timeouts,
    box_task,
    box_wide,
    check_schema_name,
    database_named,
    open_pool,
    schemas_of,
    scope_to,
    unbounded,
)
from tests.conftest import DSN, HELD_PAST_THE_TIMEOUT_S, as_the_app, postgres


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


async def idle_in_a_transaction(pool: Pool) -> None:
    """Open a transaction, say nothing past the idle timeout, then speak again."""
    async with pool.connection() as connection, connection.transaction():
        await connection.execute("select 1")
        await asyncio.sleep(HELD_PAST_THE_TIMEOUT_S)
        await connection.execute("select 1")


@postgres
async def test_a_transaction_left_idle_past_its_timeout_is_ended(impatient_pool: Pool) -> None:
    with pytest.raises(errors.IdleInTransactionSessionTimeout):
        await idle_in_a_transaction(impatient_pool)


@postgres
async def test_a_connection_of_the_pool_is_autocommit(impatient_pool: Pool) -> None:
    async with impatient_pool.connection() as connection:
        await connection.execute("select 1")
        assert connection.autocommit
        assert connection.info.transaction_status == TransactionStatus.IDLE


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


# Migration 0096 and the pool together: a connection taken for an org sees and writes its rows
# alone, a query that forgot its WHERE included; one taken box-wide sees every org's; a task of the
# box started inside a request acts for no org.
@postgres
async def test_a_connection_taken_for_an_org_sees_and_writes_its_rows_alone(pool: Pool) -> None:
    async with pool.connection() as connection:
        await connection.execute("insert into orgs (id, slug, name) values ('org_a', 'a', 'A')")
        await connection.execute("insert into orgs (id, slug, name) values ('org_b', 'b', 'B')")

    async def slugs() -> list[str]:
        async with pool.connection() as connection:
            rows = await (
                await connection.execute("select slug from orgs order by slug")
            ).fetchall()
        return [str(row["slug"]) for row in rows]

    everyone = await slugs()
    context = contextvars.copy_context()
    context.run(scope_to, "org_a")
    scoped = await asyncio.create_task(slugs(), context=context)

    async def writes_elsewhere() -> None:
        async with pool.connection() as connection:
            await connection.execute("update orgs set name = 'taken' where id = 'org_b'")
            await connection.execute("insert into orgs (id, slug, name) values ('org_c', 'c', 'C')")

    with pytest.raises(errors.InsufficientPrivilege):
        await asyncio.create_task(writes_elsewhere(), context=context.copy())

    async def wide_inside() -> tuple[list[str], list[str]]:
        with box_wide():
            wide = await slugs()
        started = await box_task(slugs())
        return wide, started

    wide, started = await asyncio.create_task(wide_inside(), context=context.copy())
    assert everyone == ["a", "b", "default"]
    assert scoped == ["a"]
    assert wide == started == ["a", "b", "default"]
    async with pool.connection() as connection:
        row = await (
            await connection.execute("select name from orgs where id = 'org_b'")
        ).fetchone()
    assert row is not None
    assert row["name"] == "B", "the scoped update touched nothing"


# The set is told on checkout and remembered per connection. A request cancelled right after the
# server took its org, before the answer was read, must not leave a connection the server has
# moved and the pool remembers as before: the next request taking it would read the wrong org.
@postgres
async def test_a_set_cut_after_the_server_took_it_is_asked_again_on_the_next_checkout(
    schema: str,
) -> None:
    pool = await open_pool(as_the_app(DSN), schema=schema, max_size=1)
    try:
        async with pool.connection() as connection:
            await connection.execute("insert into orgs (id, slug, name) values ('org_a', 'a', 'A')")
            await connection.execute("insert into orgs (id, slug, name) values ('org_b', 'b', 'B')")

        async def slugs() -> list[str]:
            async with pool.connection() as connection:
                rows = await (
                    await connection.execute("select slug from orgs order by slug")
                ).fetchall()
            return [str(row["slug"]) for row in rows]

        for_a = contextvars.copy_context()
        for_a.run(scope_to, "org_a")
        for_b = contextvars.copy_context()
        for_b.run(scope_to, "org_b")
        assert await asyncio.create_task(slugs(), context=for_a.copy()) == ["a"]

        real = AsyncConnection[DictRow].execute

        async def set_then_cut(
            connection: AsyncConnection[DictRow], query: QueryNoTemplate, params: Params
        ) -> None:
            await real(connection, query, params)
            raise asyncio.CancelledError

        with (
            patch.object(AsyncConnection, "execute", autospec=True, side_effect=set_then_cut),
            pytest.raises(asyncio.CancelledError),
        ):
            await asyncio.create_task(slugs(), context=for_b.copy())
        assert await asyncio.create_task(slugs(), context=for_a.copy()) == ["a"]
    finally:
        await pool.close()
