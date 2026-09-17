"""The members and invitations tables in Postgres, driven exactly as the memory twin is."""

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest

from pinecall.auth.members_postgres import PostgresMembers
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
    assert invited.token is not None
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
    assert first.token is not None and second.token is not None
    assert await members.accept(first.token, A_HASH) is None
    changed = await members.update(org, first.member.id, role="supervisor")
    assert changed is not None and (changed.role, changed.status) == ("supervisor", "invited")
    assert await members.update(org, "m_nobody", role="qa") is None
    assert await members.find("org_nobody", first.member.id) is None


async def test_a_reset_link_sets_an_active_members_password_and_never_revives_a_disabled_one(
    pool: Pool, org: str
) -> None:
    members = PostgresMembers(pool)
    email = f"ana-{uuid4().hex[:8]}@clinica.uy"
    invited = await members.invite(org, email, "Ana", "qa", [])
    assert invited is not None and invited.token is not None
    assert await members.reset(org, invited.member.id) is None, "invited is not active"
    await members.accept(invited.token, A_HASH)
    first = await members.reset(org, invited.member.id)
    second = await members.reset(org, invited.member.id)
    assert first is not None and first.token is not None
    assert second is not None and second.token is not None
    assert await members.accept(first.token, "new") is None, "the newest link is the only link"
    assert await members.accept(second.token, "$argon2id$new") is not None
    assert await members.a_persons_password(email) == "$argon2id$new"
    third = await members.reset(org, invited.member.id)
    assert third is not None and third.token is not None
    await members.update(org, invited.member.id, status="disabled")
    assert await members.accept(third.token, "again") is None
    found = await members.find(org, invited.member.id)
    assert found is not None and found.status == "disabled"


async def test_removing_deletes_the_row_and_cascades_to_its_links_within_the_org(
    pool: Pool, org: str
) -> None:
    members = PostgresMembers(pool)
    invited = await members.invite(org, f"ana-{uuid4().hex[:8]}@clinica.uy", "Ana", "qa", [])
    assert invited is not None and invited.token is not None
    assert await members.remove("org_nobody", invited.member.id) is False
    assert await members.remove(org, invited.member.id) is True
    assert await members.remove(org, invited.member.id) is False, "gone is gone"
    assert await members.listed(org) == () and await members.seated(org) == 0
    assert await members.accept(invited.token, A_HASH) is None, "the link went with the row"
    left = await pool.fetchrow(
        "SELECT count(*) AS links FROM invitations WHERE member = $1", invited.member.id
    )
    assert left is not None and int(left["links"]) == 0
