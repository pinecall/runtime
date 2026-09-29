"""Tests for retention: an org's days, the calls past them, and the run that erases only those."""

from pathlib import Path

from pinecall.domain.scope import Scope
from pinecall.log.store import Store
from pinecall.postgres.pool import Pool
from pinecall.tenancy import erasure, retention
from pinecall.wire.rest.accounts import OrgPolicy
from tests.conftest import postgres
from tests.log.conftest import ACall, logged_call
from tests.tenancy.conftest import an_org

pytestmark = postgres

A_DAY_S = 86400.0

# logged_call starts every call at started_at 1.0 (the store's ticking clock); "now" is days after.
DAYS_LATER = 1.0 + 40 * A_DAY_S


async def test_an_org_nobody_set_keeps_everything_and_says_nobody_set_it(pool: Pool) -> None:
    org = await an_org(pool)
    row = await retention.policy_of(pool, org.id)
    assert (row.policy.retention_days, row.set_by, row.set_at) == (None, None, None)


async def test_a_policy_is_replaced_whole_and_names_who_set_it(pool: Pool) -> None:
    org = await an_org(pool)
    await retention.put_policy(pool, org.id, OrgPolicy(retention_days=30), by="m_1")
    await retention.put_policy(pool, org.id, OrgPolicy(retention_days=90), by="m_2")
    row = await retention.policy_of(pool, org.id)
    assert (row.policy.retention_days, row.set_by) == (90, "m_2")
    assert row.set_at is not None


async def test_only_sealed_calls_past_their_orgs_days_are_due(pool: Pool, store: Store) -> None:
    keeping = await an_org(pool)
    forever = await an_org(pool, "para-siempre")
    old = await logged_call(store, keeping.id)
    running = await logged_call(store, keeping.id, ACall(ended=False))
    theirs = await logged_call(store, forever.id)
    await retention.put_policy(pool, keeping.id, OrgPolicy(retention_days=30), by="m_1")

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
    await retention.put_policy(pool, org.id, OrgPolicy(retention_days=7), by="m_1")

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
    await retention.put_policy(pool, org.id, OrgPolicy(retention_days=1), by="m_1")
    assert len(await retention.purge(pool, tmp_path, DAYS_LATER, limit=2)) == 2
    assert len(await retention.purge(pool, tmp_path, DAYS_LATER, limit=2)) == 1
    for call in calls:
        assert await store.whole(call) == []
