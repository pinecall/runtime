"""A sandbox's mirror of production's orgs: kept by production's id, never over a slug it holds."""

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest

from pinecall.log.store import Pool, open_pool
from pinecall.orgs.records import MemoryOrgs, PostgresOrgs
from pinecall.types import Org
from tests.postgres import Dev

TIENDA = Org(id="org_4ad9", slug="tienda", name="Tienda Sur")


@pytest.mark.unit
async def test_a_mirrored_org_is_kept_by_productions_id_and_renamed_by_the_next_mirror() -> None:
    orgs = MemoryOrgs()
    assert await orgs.mirrored(TIENDA) == TIENDA
    renamed = Org(id=TIENDA.id, slug="tienda-sur", name="Tienda del Sur")
    assert await orgs.mirrored(renamed) == renamed
    assert await orgs.find("tienda-sur") == renamed
    assert await orgs.find("tienda") is None


@pytest.mark.unit
async def test_a_slug_another_org_holds_here_is_not_mirrored_over() -> None:
    orgs = MemoryOrgs()
    ours = await orgs.create("tienda", "ours")
    assert await orgs.mirrored(TIENDA) is None
    assert await orgs.find("tienda") == ours


@pytest.fixture
async def pool(postgres: Dev) -> AsyncIterator[Pool]:
    """A pool on this run's schema."""
    pool = await open_pool(postgres.dsn, schema=postgres.schema)
    try:
        yield pool
    finally:
        await pool.close()


@pytest.mark.postgres
async def test_the_table_upserts_by_id_and_refuses_a_slug_another_row_holds(pool: Pool) -> None:
    """The same statement a sandbox runs at every sign-in, against the real UNIQUE on the slug."""
    orgs = PostgresOrgs(pool)
    word = uuid4().hex[:12]
    mirrored = Org(id=f"org_{word}", slug=f"tienda-{word}", name="Tienda Sur")
    assert await orgs.mirrored(mirrored) == mirrored
    renamed = Org(id=mirrored.id, slug=f"sur-{word}", name="Tienda del Sur")
    assert await orgs.mirrored(renamed) == renamed
    ours = await orgs.create(f"ours-{word}", "ours")
    assert ours is not None
    assert await orgs.mirrored(Org(id=mirrored.id, slug=ours.slug, name="x")) is None
    assert await orgs.find(mirrored.id) == renamed
