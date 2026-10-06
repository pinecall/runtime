"""Tests for the log's days: the swap of 0027, days made ahead, the default, days dropped."""

from datetime import UTC, datetime

from psycopg import sql

from pinecall.log import days
from pinecall.log.store import Store
from pinecall.postgres.pool import Pool
from tests.conftest import postgres

pytestmark = postgres

AGENT = "dental-sur"

WHERE = "select tableoid::regclass::text as part from call_log where call = %(call)s"

BOUND = """
select (regexp_match(pg_get_expr(relpartbound, oid), 'TO \\(''?(-?[0-9.e+]+)'))[1]::float8 as upto
from pg_class where oid = 'call_log_before_the_days'::regclass
"""


def day_named(start: float) -> str:
    return f"call_log_{datetime.fromtimestamp(start, UTC):%Y%m%d}"


async def bound_of(pool: Pool) -> float:
    async with pool.connection() as connection:
        row = await (await connection.execute(BOUND)).fetchone()
    assert row is not None
    return float(row["upto"])


async def partition_of(pool: Pool, call: str) -> str:
    async with pool.connection() as connection:
        row = await (await connection.execute(WHERE, {"call": call})).fetchone()
    assert row is not None
    return str(row["part"]).rsplit(".", 1)[-1]


async def test_an_entry_lands_in_the_day_of_its_time_and_past_the_days_in_the_default(
    pool: Pool, call: str
) -> None:
    bound = await bound_of(pool)
    for suffix, at in (("old", 1.0), ("day", bound + 3600), ("far", bound + 400 * 86400)):
        store = Store(pool, clock=lambda at=at: at)
        await store.append(f"{call}-{suffix}", AGENT, "turn.user", {}, ephemeral=False)
        await store.writer.drained()
    assert await partition_of(pool, f"{call}-old") == days.BEFORE
    assert await partition_of(pool, f"{call}-day") == day_named(bound)
    assert await partition_of(pool, f"{call}-far") == days.DEFAULT
    trouble = await days.examined(pool, bound)
    assert trouble is not None
    assert "1 rows in call_log_default" in trouble


async def test_a_night_makes_the_days_ahead_refuses_one_the_default_holds_and_drops_the_empty(
    owners_pool: Pool, call: str
) -> None:
    bound = await bound_of(owners_pool)
    later = bound + 10 * 86400
    store = Store(owners_pool, clock=lambda: later + 60)
    await store.append(call, AGENT, "turn.user", {}, ephemeral=False)
    await store.writer.drained()
    kept = await days.kept(owners_pool, later)
    assert kept.refused == [day_named(later)]
    assert kept.made == [day_named(later + n * 86400) for n in range(1, days.AHEAD)]
    assert day_named(bound) in kept.dropped
    assert days.BEFORE in kept.dropped, "the table before the days was empty and past"
    async with owners_pool.connection() as connection:
        query = sql.SQL("select count(*) as n from {}").format(sql.Identifier(days.DEFAULT))
        row = await (await connection.execute(query)).fetchone()
    assert row is not None
    assert row["n"] == 1
