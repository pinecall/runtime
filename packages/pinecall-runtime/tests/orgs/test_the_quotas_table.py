"""The quotas row in Postgres: replaced whole, NULL is no limit and zero is a limit of zero."""

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest

from pinecall.db import Pool, open_pool
from pinecall.orgs.org_sso import PostgresSso
from pinecall.orgs.records import PostgresOrgs
from pinecall.orgs.vault import build_cipher
from pinecall.orgs.widgets import PostgresWidgets, Widget
from pinecall.types import QUOTAS, OrgSso, Quotas
from tests.api.conftest import A_VAULT_KEY
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
            llm_tokens=3_000_000_000,
        ),
    )
    kept = await orgs.quotas_of(org)
    assert (kept.memory_facts, kept.knowledge_chunks, kept.numbers) == (0, 5000, 1)
    assert kept.llm_tokens == 3_000_000_000, "past an integer: the column is a bigint"
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


async def test_the_budget_round_trips_beside_the_quotas_it_is_not_one_of(
    pool: Pool, org: str
) -> None:
    orgs = PostgresOrgs(pool)
    await orgs.set_quotas(org, Quotas(seats=4, budget_eur=250))
    assert await orgs.quotas_of(org) == Quotas(seats=4, budget_eur=250)
    assert "budget_eur" not in QUOTAS, "nothing is refused over a budget"


async def test_an_org_is_judged_until_somebody_turns_it_off(pool: Pool, org: str) -> None:
    orgs = PostgresOrgs(pool)
    assert await orgs.judges(org) is True
    await orgs.set_judging(org, False)
    assert await orgs.judges(org) is False
    await orgs.set_judging(org, True)
    assert await orgs.judges(org) is True


async def test_a_widget_round_trips_per_world(pool: Pool, org: str) -> None:
    widgets = PostgresWidgets(pool)
    assert await widgets.of(org, "production", "clinica") == Widget()
    kept = Widget(title="Clínica", greeting="Hola", accent="#cd58b2", autostart=True, theme="dark")
    await widgets.put(org, "production", "clinica", kept)
    await widgets.put(org, "production", "clinica", kept)
    assert await widgets.of(org, "production", "clinica") == kept
    assert await widgets.of(org, "sandbox", "clinica") == Widget()


# The box's own Fernet is the suite's one word for it (tests/api/conftest.py), as the carriers'
# own fixtures already borrow it: a key generated per run would seal a row no assertion can name.
A_SECRET = "a-client-secret-nobody-will-ever-deploy"


async def test_an_orgs_provider_round_trips_and_the_secret_is_not_in_the_row(
    pool: Pool, org: str
) -> None:
    """One row per org, replaced whole, and the client secret a Fernet token in the column."""
    sso = PostgresSso(pool, build_cipher(A_VAULT_KEY))
    assert await sso.of(org) is None
    wired = OrgSso(
        org=org,
        issuer="https://idp.test",
        client_id="the-gateway-at-the-idp",
        client_secret=A_SECRET,
        domains=("tiendasur.uy", "clinica.test"),
        role="developer",
    )
    await sso.put(wired)
    await sso.put(wired)
    assert await sso.of(org) == wired
    kept = await pool.fetchrow("SELECT ciphertext FROM org_sso WHERE org = $1", org)
    assert kept is not None and A_SECRET not in str(kept["ciphertext"])
    # The one read that spans orgs: what a sign-in page's discovery asks.
    assert [one.org for one in await sso.with_domain("tiendasur.uy")] == [org]
    assert await sso.with_domain("elsewhere.test") == ()
    assert await sso.drop(org) is True
    assert await sso.drop(org) is False


async def test_what_the_box_lends_round_trips_and_null_and_empty_stay_apart(
    pool: Pool, org: str
) -> None:
    """NULL lends every key and an empty array lends none: two answers, never one."""
    orgs = PostgresOrgs(pool)
    trial = frozenset({"deepgram", "anthropic/claude-haiku-4-5"})
    await orgs.set_quotas(org, Quotas(minutes=30, lends=trial))
    assert (await orgs.quotas_of(org)).lends == trial
    await orgs.set_quotas(org, Quotas(lends=frozenset()))
    assert (await orgs.quotas_of(org)).lends == frozenset()
    await orgs.set_quotas(org, Quotas())
    assert (await orgs.quotas_of(org)).lends is None
