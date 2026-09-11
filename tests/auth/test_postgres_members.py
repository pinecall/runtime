"""The members and invitations tables in Postgres, driven exactly as the memory twin is."""

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest

from pinecall.auth.members import PostgresMembers
from pinecall.log.store import Pool, open_pool
from pinecall.orgs.table import PostgresOrgs
from tests.postgres import Dev

pytestmark = pytest.mark.postgres

A_HASH = "$argon2id$not-really-but-the-table-does-not-care"


@pytest.fixture
async def pool(postgres: Dev) -> AsyncIterator[Pool]:
    pool = await open_pool(postgres.dsn, schema=postgres.schema)
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture
async def org(pool: Pool) -> str:
    """A tenant of this test's own: members reference orgs, and (org, email) is unique."""
    slug = f"org-{uuid4().hex[:12]}"
    created = await PostgresOrgs(pool).create(slug, slug)
    assert created is not None
    return created.id


async def test_invite_accept_and_login_read_round_trip_through_the_two_tables(
    pool: Pool, org: str
) -> None:
    members = PostgresMembers(pool)
    invited = await members.invite(org, "berna@clinica.uy", "Berna", "developer", ["clinica-norte"])
    assert invited is not None
    assert [m.status for m in await members.listed(org)] == ["invited"]
    accepted = await members.accept(invited.token, A_HASH)
    assert accepted is not None
    assert (accepted.status, accepted.role, accepted.agents) == (
        "active",
        "developer",
        frozenset({"clinica-norte"}),
    )
    assert await members.accept(invited.token, A_HASH) is None, "spent, atomically"
    kept = await members.by_email(org, "berna@clinica.uy")
    assert kept is not None and kept.password_hash == A_HASH
    assert await members.invite(org, "berna@clinica.uy", "Berna", "qa", []) is None


async def test_a_re_invite_spends_the_older_token_and_an_update_coalesces(
    pool: Pool, org: str
) -> None:
    members = PostgresMembers(pool)
    first = await members.invite(org, "ana@clinica.uy", "Ana", "qa", [])
    second = await members.invite(org, "ana@clinica.uy", "Ana", "qa", [])
    assert first is not None and second is not None
    assert await members.accept(first.token, A_HASH) is None
    changed = await members.update(org, first.member.id, role="supervisor")
    assert changed is not None and (changed.role, changed.status) == ("supervisor", "invited")
    assert await members.update(org, "m_nobody", role="qa") is None
    assert await members.find("org_nobody", first.member.id) is None
