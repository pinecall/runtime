"""A person into an org and out of it: an address already seated, the removal, the last admin."""

import re

import pytest

from pinecall.accounts import (
    AlreadyAMember,
    Invitee,
    LastAdmin,
    NoSuchMember,
    invite_member,
    remove_member,
)
from pinecall.auth.keys_memory import MemoryKeys
from pinecall.auth.members_memory import MemoryMembers
from pinecall.mail import Outbox
from pinecall.types import Member, Org, Role

pytestmark = pytest.mark.unit

CLINICA = Org(id="org_clinica", slug="clinica", name="Clínica Norte")
A_HASH = "$argon2id$not-really-but-the-table-does-not-care"
BASE = "https://box.example"


def no_mail() -> Outbox:
    """A box that sends no mail and an org with no account of its own: nothing is ever posted."""
    return Outbox(None, None)


async def an_active(members: MemoryMembers, email: str, role: Role = "admin") -> Member:
    invited = await members.invite(CLINICA.id, email, email.partition("@")[0], role, [])
    assert invited is not None and invited.token is not None
    accepted = await members.accept(invited.token, A_HASH)
    assert accepted is not None
    return accepted


async def test_an_invitation_hands_the_token_over_and_says_no_letter_went() -> None:
    members = MemoryMembers()
    invitee = Invitee("ana@clinica.uy", "Ana", "developer")
    made = await invite_member(members, CLINICA, invitee, "Berna", BASE, no_mail())
    assert made.member.status == "invited" and made.token is not None
    assert made.mailed is False
    kept = await invite_member(members, CLINICA, invitee, "Berna", BASE, no_mail(), handed=False)
    assert kept.token is None, "a link that travels by mail alone is not in the answer"


async def test_an_address_that_accepted_here_is_refused_in_the_sentence_that_names_it() -> None:
    members = MemoryMembers()
    await an_active(members, "ana@clinica.uy", "developer")
    with pytest.raises(AlreadyAMember, match=re.escape("ana@clinica.uy")):
        await invite_member(
            members, CLINICA, Invitee("ana@clinica.uy", "Ana", "qa"), "Berna", BASE, no_mail()
        )


async def test_the_last_active_admin_stays_and_a_second_one_lets_the_first_go() -> None:
    members, keys = MemoryMembers(), MemoryKeys()
    first = await an_active(members, "berna@clinica.uy")
    with pytest.raises(LastAdmin, match=re.escape("berna@clinica.uy")):
        await remove_member(members, keys, CLINICA.id, first.id)
    await an_active(members, "ana@clinica.uy")
    await remove_member(members, keys, CLINICA.id, first.id)
    assert [one.email for one in await members.listed(CLINICA.id)] == ["ana@clinica.uy"]


async def test_removing_revokes_every_key_of_theirs_before_the_row_goes() -> None:
    members, keys = MemoryMembers(), MemoryKeys()
    await an_active(members, "berna@clinica.uy")
    ana = await an_active(members, "ana@clinica.uy", "developer")
    issued = await keys.issue(CLINICA.id, "laptop", subject=ana.id, name=ana.name)
    await remove_member(members, keys, CLINICA.id, ana.id)
    assert await keys.verify(issued.key) is None


async def test_nobody_by_that_id_is_the_404_sentence() -> None:
    with pytest.raises(NoSuchMember, match="m_nobody"):
        await remove_member(MemoryMembers(), MemoryKeys(), CLINICA.id, "m_nobody")
