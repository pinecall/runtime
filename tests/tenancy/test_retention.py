"""Tests for retention: an org's days, the calls past them, the run, call records past 24 months."""

from pathlib import Path

from pinecall.domain.scope import Scope
from pinecall.log.store import Store
from pinecall.postgres.pool import Pool
from pinecall.tenancy import erasure, policy, retention, traceback
from pinecall.wire.rest.accounts import OrgPolicy
from tests.conftest import outlasting_a_lock, postgres
from tests.log.conftest import ACall, logged_call
from tests.tenancy.conftest import an_org

pytestmark = postgres

A_DAY_S = 86400.0

A_DIAL = """
INSERT INTO dials (org, env, agent, dialled, shown, asked_by, at)
VALUES (%(org)s, 'production', 'agenda', '+14155550142', '+14155550100', 'm_ana', now())
"""

# A dial nobody answered: facts with an end and no start.
A_RECORD_WITH_NO_START = """
INSERT INTO call_records (call, org, env, direction, from_number, to_number, ended_at)
VALUES ('CA_unanswered', %(org)s, 'production', 'outbound', '+14155550100', '+14155550142', 1.0)
"""

# logged_call starts every call at started_at 1.0 (the store's ticking clock); "now" is days after.
DAYS_LATER = 1.0 + 40 * A_DAY_S


async def test_only_sealed_calls_past_their_orgs_days_are_due(pool: Pool, store: Store) -> None:
    keeping = await an_org(pool)
    forever = await an_org(pool, "para-siempre")
    old = await logged_call(store, keeping.id)
    running = await logged_call(store, keeping.id, ACall(ended=False))
    theirs = await logged_call(store, forever.id)
    await policy.put_policy(pool, keeping.id, OrgPolicy(retention_days=30), by="m_1")

    due = await retention.due(pool, DAYS_LATER)

    assert [call.call for call in due] == [old]
    assert due[0].scope == Scope(keeping.id)
    assert running not in [call.call for call in due]
    assert theirs not in [call.call for call in due]
    assert await retention.due(pool, 1.0 + 10 * A_DAY_S) == []


async def test_the_run_erases_what_is_due_through_the_trail_as_retention(
    pool: Pool, store: Store, tmp_path: Path
) -> None:
    org = await an_org(pool)
    first = await logged_call(store, org.id)
    second = await logged_call(store, org.id)
    await policy.put_policy(pool, org.id, OrgPolicy(retention_days=7), by="m_1")

    erased = await retention.purge(pool, tmp_path, DAYS_LATER)

    assert sorted(erased) == sorted([first, second])
    assert await store.whole(first) == []
    trail = await erasure.trail(pool, org.id)
    assert {(row.what, row.asked_by) for row in trail} == {("call", "retention")}
    assert await retention.purge(pool, tmp_path, DAYS_LATER) == []


async def test_a_run_stops_at_its_limit_and_the_next_one_takes_the_rest(
    pool: Pool, store: Store, tmp_path: Path
) -> None:
    org = await an_org(pool)
    calls = [await logged_call(store, org.id) for _ in range(3)]
    await policy.put_policy(pool, org.id, OrgPolicy(retention_days=1), by="m_1")
    assert len(await retention.purge(pool, tmp_path, DAYS_LATER, limit=2)) == 2
    assert len(await retention.purge(pool, tmp_path, DAYS_LATER, limit=2)) == 1
    for call in calls:
        assert await store.whole(call) == []


async def test_dials_and_a_record_of_a_call_that_never_started_are_forgotten_at_24_months(
    pool: Pool,
) -> None:
    org = await an_org(pool)
    async with pool.connection() as connection:
        await connection.execute(A_DIAL, {"org": org.id})
        await connection.execute(A_RECORD_WITH_NO_START, {"org": org.id})
    assert await retention.forget_dials(pool, 1.0 + 700 * A_DAY_S) == 0
    assert await retention.forget_records(pool, 1.0 + 700 * A_DAY_S) == 0
    assert await retention.forget_dials(pool, 4_000_000_000.0) == 1
    assert await retention.forget_records(pool, 4_000_000_000.0) == 1


async def test_an_erased_calls_record_is_kept_24_months_and_then_forgotten(
    pool: Pool, store: Store, tmp_path: Path
) -> None:
    org = await an_org(pool)
    call = await logged_call(store, org.id, ACall(caller="+34600555666"))
    await erasure.call(pool, tmp_path, Scope(org.id), call, by="m_1")
    await retention.forget_records(pool, 1.0 + 700 * A_DAY_S)
    assert [kept.call for kept in (await traceback.of_number(pool, "+34600555666", 0)).calls] == [
        call
    ]
    assert await retention.forget_records(pool, 1.0 + 731 * A_DAY_S) >= 1
    assert (await traceback.of_number(pool, "+34600555666", 0)).calls == []


async def test_the_nightly_run_reads_and_forgets_past_the_pools_statement_timeout(
    pool: Pool, impatient_pool: Pool, schema: str
) -> None:
    org = await an_org(pool)
    async with pool.connection() as connection:
        await connection.execute(A_DIAL, {"org": org.id})
    later = 4_000_000_000.0

    async def due() -> list[retention.Due]:
        return await retention.due(impatient_pool, later)

    async def forgotten() -> int:
        return await retention.forget_dials(impatient_pool, later)

    assert await outlasting_a_lock(schema, "call_log_head", due) == []
    assert await outlasting_a_lock(schema, "dials", forgotten) == 1
