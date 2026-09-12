"""The members table in memory: invited with a token, accepted with a password, changed, found."""

import pytest

from pinecall.auth.invitations import INVITATION_PREFIX
from pinecall.auth.members import INVITATION_TTL_S, MemoryMembers

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
    clock.now += INVITATION_TTL_S
    assert await members.accept(invited.token, A_HASH) is None
    assert await members.accept("inv_nobody", A_HASH) is None


async def test_re_inviting_someone_still_invited_replaces_the_token() -> None:
    members = MemoryMembers()
    first = await members.invite(ORG, "berna@clinica.uy", "Berna", "qa", [])
    second = await members.invite(ORG, "berna@clinica.uy", "Berna", "qa", [])
    assert first is not None and second is not None
    assert first.member.id == second.member.id
    assert await members.accept(first.token, A_HASH) is None
    assert await members.accept(second.token, A_HASH) is not None


async def test_someone_who_accepted_is_not_re_invited() -> None:
    members = MemoryMembers()
    invited = await members.invite(ORG, "berna@clinica.uy", "Berna", "qa", [])
    assert invited is not None
    await members.accept(invited.token, A_HASH)
    assert await members.invite(ORG, "berna@clinica.uy", "Berna", "qa", []) is None


async def test_an_update_replaces_only_what_was_named_and_stays_within_the_org() -> None:
    members = MemoryMembers()
    invited = await members.invite(ORG, "berna@clinica.uy", "Berna", "qa", ["a"])
    assert invited is not None
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
