"""The members table in memory: invited with a token, accepted with a password, changed, found."""

import pytest

from pinecall.auth.invitations import INVITATION_PREFIX, INVITATION_TTL_S
from pinecall.auth.members_memory import MemoryMembers

pytestmark = pytest.mark.unit

ORG = "clinica"
A_HASH = "$argon2id$not-really-but-the-table-does-not-care"


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def test_an_invite_makes_a_row_still_invited_and_a_token_shown_once() -> None:
    members = MemoryMembers()
    invited = await members.invite(ORG, "berna@clinica.uy", "Berna", "developer", ["clinica-norte"])
    assert invited is not None
    assert invited.token is not None
    assert invited.token.startswith(INVITATION_PREFIX)
    assert invited.member.status == "invited"
    assert invited.member.agents == frozenset({"clinica-norte"})
    assert [m.id for m in await members.listed(ORG)] == [invited.member.id]
    kept = await members.by_email(ORG, "berna@clinica.uy")
    assert kept is not None and kept.password_hash is None


async def test_accepting_spends_the_token_sets_the_password_and_makes_the_member_active() -> None:
    members = MemoryMembers()
    invited = await members.invite(ORG, "berna@clinica.uy", "Berna", "qa", [])
    assert invited is not None
    assert invited.token is not None
    accepted = await members.accept(invited.token, A_HASH)
    assert accepted is not None and accepted.status == "active"
    assert await members.accept(invited.token, A_HASH) is None, "once"
    kept = await members.by_email(ORG, "berna@clinica.uy")
    assert kept is not None and kept.password_hash == A_HASH


async def test_an_expired_or_unknown_token_accepts_nobody() -> None:
    clock = _Clock()
    members = MemoryMembers(clock=clock)
    invited = await members.invite(ORG, "berna@clinica.uy", "Berna", "qa", [])
    assert invited is not None
    assert invited.token is not None
    clock.now += INVITATION_TTL_S
    assert await members.accept(invited.token, A_HASH) is None
    assert await members.accept("inv_nobody", A_HASH) is None


async def test_re_inviting_someone_still_invited_replaces_the_token() -> None:
    members = MemoryMembers()
    first = await members.invite(ORG, "berna@clinica.uy", "Berna", "qa", [])
    second = await members.invite(ORG, "berna@clinica.uy", "Berna", "qa", [])
    assert first is not None and second is not None
    assert first.token is not None and second.token is not None
    assert first.member.id == second.member.id
    assert await members.accept(first.token, A_HASH) is None
    assert await members.accept(second.token, A_HASH) is not None


async def test_someone_who_accepted_is_not_re_invited() -> None:
    members = MemoryMembers()
    invited = await members.invite(ORG, "berna@clinica.uy", "Berna", "qa", [])
    assert invited is not None
    assert invited.token is not None
    await members.accept(invited.token, A_HASH)
    assert await members.invite(ORG, "berna@clinica.uy", "Berna", "qa", []) is None


async def test_an_update_replaces_only_what_was_named_and_stays_within_the_org() -> None:
    members = MemoryMembers()
    invited = await members.invite(ORG, "berna@clinica.uy", "Berna", "qa", ["a"])
    assert invited is not None
    assert invited.token is not None
    changed = await members.update(ORG, invited.member.id, role="manager")
    assert changed is not None
    assert (changed.role, changed.agents, changed.status) == (
        "manager",
        frozenset({"a"}),
        "invited",
    )
    disabled = await members.update(ORG, invited.member.id, status="disabled", agents=[])
    assert disabled is not None and disabled.status == "disabled" and disabled.agents == frozenset()
    assert await members.update("tienda", invited.member.id, role="qa") is None
    assert await members.find("tienda", invited.member.id) is None
    assert await members.find(ORG, invited.member.id) == disabled


async def test_removing_takes_the_row_and_its_links_and_stays_within_the_org() -> None:
    members = MemoryMembers()
    invited = await members.invite(ORG, "ana@clinica.uy", "Ana", "qa", [])
    assert invited is not None and invited.token is not None
    assert await members.remove("another-org", invited.member.id) is False
    assert await members.remove(ORG, invited.member.id) is True
    assert await members.remove(ORG, invited.member.id) is False, "gone is gone"
    assert await members.listed(ORG) == () and await members.seated(ORG) == 0
    assert await members.accept(invited.token, A_HASH) is None, "the link went with the row"


async def test_only_a_vouched_link_proves_the_address_and_only_a_proved_one_is_seated_at_once() -> (
    None
):
    """A link an admin was handed says nothing about who opened it; one that came by mail does."""
    members = MemoryMembers()
    handed = await members.invite(ORG, "jp@cloudacio.com", "JP", "developer", [])
    assert handed is not None and handed.token is not None
    accepted = await members.accept(handed.token, A_HASH)
    assert accepted is not None and accepted.verified is False
    assert await members.verified("jp@cloudacio.com") is False
    # Known password, unproved address: a second org invites JP like anybody, with a link.
    elsewhere = await members.invite("tienda", "jp@cloudacio.com", "JP", "qa", [], vouched=True)
    assert elsewhere is not None and elsewhere.token is not None
    assert elsewhere.member.status == "invited"
    # That link came by mail alone: accepting it proves the address, on that row.
    proved = await members.accept(elsewhere.token, "$argon2id$chosen-by-jp")
    assert proved is not None and proved.verified is True
    assert await members.verified("JP@cloudacio.com ") is True, "the address, however typed"
    # And from here a third org seats JP at once, active and verified, on the password they have.
    third = await members.invite("norte", "jp@cloudacio.com", "JP", "qa", [])
    assert third is not None and third.token is None
    assert (third.member.status, third.member.verified) == ("active", True)
    kept = await members.by_email("norte", "jp@cloudacio.com")
    assert kept is not None and kept.password_hash == "$argon2id$chosen-by-jp"


async def test_a_provider_vouching_seats_and_proves_a_row_and_never_a_disabled_one() -> None:
    members = MemoryMembers()
    invited = await members.invite(ORG, "nico@tiendasur.uy", "Nico", "developer", [])
    assert invited is not None
    seated = await members.vouched_for(ORG, invited.member.id)
    assert seated is not None and (seated.status, seated.verified) == ("active", True)
    assert await members.vouched_for("tienda", invited.member.id) is None, "fenced by the org"
    await members.update(ORG, invited.member.id, status="disabled")
    assert await members.vouched_for(ORG, invited.member.id) is None
    # A reset's link vouches the same way an invitation's does, when it went by mail alone.
    ana = await members.invite(ORG, "ana@clinica.uy", "Ana", "qa", [])
    assert ana is not None and ana.token is not None
    await members.accept(ana.token, A_HASH)
    reset = await members.reset(ORG, ana.member.id, vouched=True)
    assert reset is not None and reset.token is not None
    again = await members.accept(reset.token, "$argon2id$new")
    assert again is not None and again.verified is True
