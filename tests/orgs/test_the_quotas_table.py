"""The quotas row in Postgres: replaced whole, NULL is no limit and zero is a limit of zero."""

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest

from pinecall.log.store import Pool, open_pool
from pinecall.orgs.table import PostgresOrgs
from pinecall.types import QUOTAS, Quotas
from tests.postgres import Dev

pytestmark = pytest.mark.postgres


@pytest.fixture
async def pool(postgres: Dev) -> AsyncIterator[Pool]:
    """A pool on this run's schema."""
    pool = await open_pool(postgres.dsn, schema=postgres.schema)
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture
async def org(pool: Pool) -> str:
    """A tenant of this test's own, since a quotas row is one per org and outlives the test."""
    slug = f"org-{uuid4().hex[:12]}"
    created = await PostgresOrgs(pool).create(slug, slug)
    assert created is not None
    return created.id


async def test_an_org_nobody_limited_has_no_row_and_no_limits(pool: Pool, org: str) -> None:
    """The mechanism with no numbers in it: what a self-hosted box has, by never writing a row."""
    assert await PostgresOrgs(pool).quotas_of(org) == Quotas()


async def test_every_quota_round_trips_and_zero_comes_back_as_zero_and_not_as_no_limit(
    pool: Pool, org: str
) -> None:
    """The column is nullable and the difference is the whole design: NULL is none, 0 is a limit."""
    orgs = PostgresOrgs(pool)
    await orgs.set_quotas(
        org,
        Quotas(
            minutes=100,
            messages=200,
            agents=3,
            concurrent_calls=4,
            memory_facts=0,
            knowledge_chunks=5000,
            numbers=1,
        ),
    )
    kept = await orgs.quotas_of(org)
    assert (kept.memory_facts, kept.knowledge_chunks, kept.numbers) == (0, 5000, 1)
    assert kept.switched_off("memory_facts")
    assert not kept.switched_off("knowledge_chunks")


async def test_the_set_is_replaced_whole_so_a_limit_left_out_stops_being_one(
    pool: Pool, org: str
) -> None:
    orgs = PostgresOrgs(pool)
    await orgs.set_quotas(org, Quotas(memory_facts=10, knowledge_chunks=10))
    await orgs.set_quotas(org, Quotas(knowledge_chunks=20))
    kept = await orgs.quotas_of(org)
    assert kept == Quotas(knowledge_chunks=20)
    assert all(getattr(kept, name) is None for name in QUOTAS if name != "knowledge_chunks")


async def test_removing_the_org_takes_its_quotas_with_it(pool: Pool, org: str) -> None:
    """One DELETE is the whole removal: the foreign key cascades into the quotas row."""
    orgs = PostgresOrgs(pool)
    await orgs.set_quotas(org, Quotas(memory_facts=1))
    assert await orgs.remove(org) is True
    row = await pool.fetchrow("select org from quotas where org = $1", org)
    assert row is None
