"""The members and invitations tables in Postgres, driven exactly as the memory twin is."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from uuid import uuid4

import pytest

from pinecall.auth.members import NoSeatLeft
from pinecall.auth.members_postgres import PostgresMembers
from pinecall.log.store import Pool, open_pool
from pinecall.orgs.table import PostgresOrgs
from pinecall.types import Member
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


async def test_a_vouched_link_proves_the_address_and_a_proved_person_is_seated_at_once(
    pool: Pool, org: str
) -> None:
    """0048, through the two tables: `invitations.vouched` into `members.verified_at`."""
    members = PostgresMembers(pool)
    email = f"jp-{uuid4().hex[:8]}@cloudacio.com"
    handed = await members.invite(org, email, "JP", "developer", [])
    assert handed is not None and handed.token is not None
    accepted = await members.accept(handed.token, A_HASH)
    assert accepted is not None and accepted.verified is False
    assert await members.verified(email) is False
    other = await PostgresOrgs(pool).create(f"org-{uuid4().hex[:12]}", "Other")
    assert other is not None
    # Unproved: the second org gets an invited row and a link, never a seat on that password.
    elsewhere = await members.invite(other.id, email, "JP", "qa", [], vouched=True)
    assert elsewhere is not None and elsewhere.token is not None
    assert elsewhere.member.status == "invited"
    proved = await members.accept(elsewhere.token, "$argon2id$chosen")
    assert proved is not None and proved.verified is True
    assert await members.verified(email) is True
    # A provider's word does the same, and never revives a disabled row.
    vouched = await members.vouched_for(org, accepted.id)
    assert vouched is not None and vouched.verified is True
    await members.update(org, accepted.id, status="disabled")
    assert await members.vouched_for(org, accepted.id) is None


async def test_a_mirrored_member_is_upserted_by_productions_id_over_a_stale_row_of_the_address(
    pool: Pool, org: str
) -> None:
    """The statements a sandbox runs at every sign-in: inserted, written over, a stale row out."""
    members = PostgresMembers(pool)
    berna = Member(
        id=f"m_{uuid4().hex[:12]}",
        org=org,
        email="Berna@Clinica.uy",
        name="Berna",
        role="developer",
        agents=frozenset({"clinica-norte"}),
        status="active",
    )
    first = await members.mirrored(berna)
    assert first is not None
    assert (first.email, first.verified, first.agents) == (
        "berna@clinica.uy",
        True,
        frozenset({"clinica-norte"}),
    )
    kept = await members.by_email(org, "berna@clinica.uy")
    assert kept is not None and kept.password_hash is None
    again = await members.mirrored(replace(berna, role="qa", status="disabled"))
    assert again is not None and (again.role, again.status) == ("qa", "disabled")
    again_invited = replace(berna, id=f"m_{uuid4().hex[:12]}")
    assert await members.mirrored(again_invited) is not None, "the stale row gives the address up"
    assert [m.id for m in await members.listed(org)] == [again_invited.id]


async def test_the_write_judges_the_seats_under_a_lock_so_five_at_once_seat_two(
    pool: Pool, org: str
) -> None:
    """Two invitations judged at once both saw one seat left, until the INSERT counted itself."""
    members = PostgresMembers(pool)
    outcomes = await asyncio.gather(
        *(members.invite(org, f"p{n}@x.uy", f"P{n}", "qa", (), seats=2) for n in range(5)),
        return_exceptions=True,
    )
    assert await members.seated(org) == 2
    assert sum(1 for one in outcomes if isinstance(one, NoSeatLeft)) == 3
    with pytest.raises(NoSeatLeft):
        await members.invite(org, "p9@x.uy", "P9", "qa", (), seats=2)
    # No limit, and a re-invite of one still invited, both still write.
    assert await members.invite(org, "p9@x.uy", "P9", "qa", (), seats=None) is not None
    assert await members.invite(org, "p9@x.uy", "P9", "qa", (), seats=3) is not None
