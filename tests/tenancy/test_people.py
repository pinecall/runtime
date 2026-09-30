"""People: invited, accepted with one password each, seated within the org."""

import asyncio
import hashlib

import pytest

from pinecall.domain.errors import Conflict, DeclarationRefused, NotFound, QuotaExhausted
from pinecall.domain.org import Org
from pinecall.postgres.pool import Pool
from pinecall.tenancy.orgs import create
from pinecall.tenancy.people import (
    Change,
    Invitee,
    accept,
    by_email,
    find,
    fingerprint,
    hash_password,
    invite,
    join,
    listed,
    make_operator,
    matches,
    orgs_of,
    password_of,
    remove,
    reset,
    seated,
    update,
    vouched,
)
from tests.conftest import postgres

ANA = Invitee(email=" Ana@Clinica.test ", name="Ana García", role="developer")
BRUNO = Invitee(email="bruno@clinica.test", name="Bruno", role="admin")
WHAT_ANA_TYPES = "correct horse battery"
FLOOR = 8


async def _an_org(pool: Pool, slug: str = "clinica-norte") -> Org:
    return await create(pool, slug, slug.title())


@postgres
async def test_an_invite_makes_a_row_still_invited_and_a_token_shown_once(pool: Pool) -> None:
    org = await _an_org(pool)
    invited = await invite(pool, org.id, ANA, seats=None)
    assert invited.member.status == "invited"
    assert invited.member.email == "ana@clinica.test"
    assert invited.token is not None
    assert invited.token.startswith("inv_")
    async with pool.connection() as connection:
        rows = await (await connection.execute("SELECT token_hash FROM invitations")).fetchall()
    assert [row["token_hash"] for row in rows] != [invited.token]


@postgres
async def test_accepting_spends_the_token_sets_the_password_and_makes_the_member_active(
    pool: Pool,
) -> None:
    org = await _an_org(pool)
    invited = await invite(pool, org.id, ANA, seats=None)
    assert invited.token is not None
    member = await accept(pool, invited.token, await hash_password(WHAT_ANA_TYPES, FLOOR))
    assert member is not None
    assert member.status == "active"
    assert await matches(WHAT_ANA_TYPES, await password_of(pool, "ana@clinica.test"))
    assert await accept(pool, invited.token, await hash_password(WHAT_ANA_TYPES, FLOOR)) is None


@postgres
async def test_an_unknown_token_accepts_nobody(pool: Pool) -> None:
    assert (
        await accept(pool, "inv_nobody-made-this", await hash_password(WHAT_ANA_TYPES, FLOOR))
        is None
    )


@postgres
async def test_re_inviting_someone_still_invited_replaces_the_token(pool: Pool) -> None:
    org = await _an_org(pool)
    first = await invite(pool, org.id, ANA, seats=None)
    second = await invite(pool, org.id, ANA, seats=None)
    assert first.token is not None
    assert second.token is not None
    assert second.member.id == first.member.id
    assert await accept(pool, first.token, await hash_password(WHAT_ANA_TYPES, FLOOR)) is None
    assert await accept(pool, second.token, await hash_password(WHAT_ANA_TYPES, FLOOR)) is not None


@postgres
async def test_an_address_that_accepted_here_is_refused_in_the_sentence_that_names_it(
    pool: Pool,
) -> None:
    org = await _an_org(pool)
    invited = await invite(pool, org.id, ANA, seats=None)
    assert invited.token is not None
    await accept(pool, invited.token, await hash_password(WHAT_ANA_TYPES, FLOOR))
    with pytest.raises(Conflict, match=r"ana@clinica\.test is a member of this org already"):
        await invite(pool, org.id, ANA, seats=None)


@postgres
async def test_a_person_proven_elsewhere_with_a_password_is_seated_at_once_with_no_link(
    pool: Pool,
) -> None:
    home = await _an_org(pool)
    invited = await invite(pool, home.id, ANA, seats=None, vouched=True)
    assert invited.token is not None
    await accept(pool, invited.token, await hash_password(WHAT_ANA_TYPES, FLOOR))
    other = await _an_org(pool, "northwind")
    seated_at_once = await invite(pool, other.id, ANA, seats=None)
    assert seated_at_once.token is None
    assert seated_at_once.member.status == "active"
    assert [item.org for item in await orgs_of(pool, "ana@clinica.test")] == [home.id, other.id]


@postgres
async def test_only_a_vouched_link_proves_the_address(pool: Pool) -> None:
    home = await _an_org(pool)
    invited = await invite(pool, home.id, ANA, seats=None)
    assert invited.token is not None
    member = await accept(pool, invited.token, await hash_password(WHAT_ANA_TYPES, FLOOR))
    assert member is not None
    assert not member.verified
    other = await _an_org(pool, "northwind")
    assert (await invite(pool, other.id, ANA, seats=None)).member.status == "invited"


@postgres
async def test_an_invitation_past_the_orgs_seats_makes_no_row_and_says_how_many_are_held(
    pool: Pool,
) -> None:
    org = await _an_org(pool)
    await invite(pool, org.id, BRUNO, seats=1)
    with pytest.raises(QuotaExhausted, match="all 1 of its seats"):
        await invite(pool, org.id, ANA, seats=1)
    assert await seated(pool, org.id) == 1


@postgres
async def test_the_write_judges_the_seats_under_a_lock_so_five_at_once_seat_two(
    pool: Pool,
) -> None:
    org = await _an_org(pool)
    people = [Invitee(email=f"p{n}@clinica.test", name=f"P{n}", role="qa") for n in range(5)]
    outcomes = await asyncio.gather(
        *(invite(pool, org.id, item, seats=2) for item in people), return_exceptions=True
    )
    assert sum(not isinstance(outcome, BaseException) for outcome in outcomes) == 2
    assert await seated(pool, org.id) == 2


@postgres
async def test_an_update_replaces_only_what_was_named_and_stays_within_the_org(
    pool: Pool,
) -> None:
    org = await _an_org(pool)
    other = await _an_org(pool, "northwind")
    member = (await invite(pool, org.id, ANA, seats=None)).member
    changed = await update(pool, org.id, member.id, Change(role="manager"))
    assert (changed.role, changed.name, changed.agents) == ("manager", "Ana García", frozenset())
    with pytest.raises(NotFound):
        await update(pool, other.id, member.id, Change(role="admin"))


@postgres
async def test_disabling_a_member_revokes_their_keys_and_frees_their_seat(pool: Pool) -> None:
    org = await _an_org(pool)
    member = (await invite(pool, org.id, ANA, seats=None)).member
    async with pool.connection() as connection:
        await connection.execute(
            "INSERT INTO api_keys (id, hash, org, env, scopes, subject) "
            "VALUES ('k_1', 'h_1', %(org)s, 'production', '{app}', %(member)s)",
            {"org": org.id, "member": member.id},
        )
    await update(pool, org.id, member.id, Change(status="disabled"))
    assert await seated(pool, org.id) == 0
    async with pool.connection() as connection:
        row = await (await connection.execute("SELECT revoked_at FROM api_keys")).fetchone()
    assert row is not None
    assert row["revoked_at"] is not None


@postgres
async def test_a_reset_link_sets_an_active_members_password_and_never_revives_a_disabled_one(
    pool: Pool,
) -> None:
    org = await _an_org(pool)
    invited = await invite(pool, org.id, ANA, seats=None)
    assert invited.token is not None
    member = await accept(pool, invited.token, await hash_password(WHAT_ANA_TYPES, FLOOR))
    assert member is not None
    link = await reset(pool, org.id, member.id)
    assert link is not None
    assert link.token is not None
    await accept(pool, link.token, await hash_password("a brand new password", FLOOR))
    assert await matches("a brand new password", await password_of(pool, member.email))
    await update(pool, org.id, member.id, Change(status="disabled"))
    assert await reset(pool, org.id, member.id) is None


@postgres
async def test_removing_takes_the_row_and_its_links_and_stays_within_the_org(pool: Pool) -> None:
    org = await _an_org(pool)
    other = await _an_org(pool, "northwind")
    member = (await invite(pool, org.id, ANA, seats=None)).member
    with pytest.raises(NotFound, match="nobody by that id"):
        await remove(pool, other.id, member.id)
    await remove(pool, org.id, member.id)
    assert await find(pool, org.id, member.id) is None
    async with pool.connection() as connection:
        links = await (await connection.execute("SELECT 1 FROM invitations")).fetchall()
    assert links == []


@postgres
async def test_the_last_active_admin_stays_and_a_second_one_lets_the_first_go(pool: Pool) -> None:
    org = await _an_org(pool)
    first = await invite(pool, org.id, BRUNO, seats=None)
    assert first.token is not None
    admin = await accept(pool, first.token, await hash_password(WHAT_ANA_TYPES, FLOOR))
    assert admin is not None
    with pytest.raises(Conflict, match="one active admin"):
        await remove(pool, org.id, admin.id)
    second = await invite(pool, org.id, Invitee("carla@clinica.test", "Carla", "admin"), seats=None)
    assert second.token is not None
    await accept(pool, second.token, await hash_password(WHAT_ANA_TYPES, FLOOR))
    await remove(pool, org.id, admin.id)
    assert [listed_one.name for listed_one in await listed(pool, org.id)] == ["Carla"]


@postgres
async def test_an_invited_member_who_has_a_password_elsewhere_is_seated_by_signing_in(
    pool: Pool,
) -> None:
    org = await _an_org(pool)
    member = (await invite(pool, org.id, ANA, seats=None)).member
    joined = await join(pool, org.id, member.id, await hash_password(WHAT_ANA_TYPES, FLOOR))
    assert joined is not None
    assert joined.status == "active"
    assert await join(pool, org.id, member.id, "again") is None


@postgres
async def test_a_provider_vouching_seats_and_proves_a_row_and_never_a_disabled_one(
    pool: Pool,
) -> None:
    org = await _an_org(pool)
    member = (await invite(pool, org.id, ANA, seats=None)).member
    seated_now = await vouched(pool, org.id, member.id)
    assert seated_now is not None
    assert (seated_now.status, seated_now.verified) == ("active", True)
    await update(pool, org.id, member.id, Change(status="disabled"))
    assert await vouched(pool, org.id, member.id) is None


@postgres
async def test_a_member_is_found_by_address_folded_and_made_an_operator_and_back(
    pool: Pool,
) -> None:
    org = await _an_org(pool)
    member = (await invite(pool, org.id, ANA, seats=None)).member
    assert await by_email(pool, org.id, "ANA@clinica.test") == member
    made = await make_operator(pool, org.id, member.id, on=True)
    assert made is not None
    assert made.operator
    taken = await make_operator(pool, org.id, member.id, on=False)
    assert taken is not None
    assert not taken.operator


async def test_the_hash_is_argon2id_carries_its_salt_and_never_the_password() -> None:
    hashed = await hash_password(WHAT_ANA_TYPES, FLOOR)
    assert hashed.startswith("$argon2id$")
    assert WHAT_ANA_TYPES not in hashed
    assert hashed != await hash_password(WHAT_ANA_TYPES, FLOOR)


async def test_the_right_password_matches_and_anything_else_does_not() -> None:
    hashed = await hash_password(WHAT_ANA_TYPES, FLOOR)
    assert await matches(WHAT_ANA_TYPES, hashed)
    assert not await matches("wrong horse battery", hashed)


async def test_nobodys_hash_matches_no_password() -> None:
    assert not await matches(WHAT_ANA_TYPES, None)


async def test_a_hash_that_is_not_one_is_a_mismatch_and_never_an_error() -> None:
    assert not await matches(WHAT_ANA_TYPES, "not-a-hash")


async def test_a_password_under_the_floor_is_refused_before_it_is_hashed() -> None:
    with pytest.raises(DeclarationRefused, match="at least 8 characters"):
        await hash_password("short", FLOOR)


async def test_the_floor_is_the_boxs_and_zero_is_no_rule_at_all() -> None:
    assert (await hash_password("", 0)).startswith("$argon2id$")


def test_the_fingerprint_is_the_secrets_sha256_and_nothing_of_the_secret_survives_it() -> None:
    kept = fingerprint("pc_live_abc")
    assert kept == hashlib.sha256(b"pc_live_abc").hexdigest()
    assert "abc" not in kept


def test_two_secrets_that_differ_by_one_character_hash_apart() -> None:
    assert fingerprint("pc_live_abc") != fingerprint("pc_live_abd")
