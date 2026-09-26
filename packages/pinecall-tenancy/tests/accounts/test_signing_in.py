"""Signing in by password: one sentence for every wrong thing, and the standings said after."""

import pytest

from pinecall.accounts import (
    NOBODY,
    NOBODY_ANYWHERE,
    NotActive,
    SigningIn,
    WrongCredentials,
    sign_in_with_password,
)
from pinecall.auth import passwords
from pinecall.auth.keys_memory import MemoryKeys
from pinecall.auth.members_memory import MemoryMembers
from pinecall.orgs.records_memory import MemoryOrgs
from pinecall.types import SANDBOX

pytestmark = pytest.mark.unit

PASSWORD = "a password long enough"


async def a_clinic() -> tuple[MemoryOrgs, MemoryMembers, str]:
    orgs, members = MemoryOrgs(), MemoryMembers()
    org = await orgs.create("clinica", "Clínica Norte")
    assert org is not None
    invited = await members.invite(org.id, "ana@clinica.uy", "Ana", "developer", [])
    assert invited is not None and invited.token is not None
    await members.accept(invited.token, await passwords.hash_password(PASSWORD, 8))
    return orgs, members, invited.member.id


async def test_the_right_password_mints_a_key_for_the_person_and_their_device() -> None:
    orgs, members, ana = await a_clinic()
    keys = MemoryKeys()
    signing_in = SigningIn("ana@clinica.uy", PASSWORD, device="laptop")
    issued = await sign_in_with_password(signing_in, orgs, members, keys, None, SANDBOX)
    assert (issued.record.subject, issued.record.label) == (ana, "laptop")


async def test_a_wrong_password_and_a_stranger_are_one_sentence() -> None:
    orgs, members, _ana = await a_clinic()
    wrong = SigningIn("ana@clinica.uy", "not the password")
    stranger = SigningIn("nobody@clinica.uy", PASSWORD, org="clinica")
    with pytest.raises(WrongCredentials, match=NOBODY_ANYWHERE):
        await sign_in_with_password(wrong, orgs, members, MemoryKeys(), None, SANDBOX)
    with pytest.raises(WrongCredentials) as refused:
        await sign_in_with_password(stranger, orgs, members, MemoryKeys(), None, SANDBOX)
    assert str(refused.value) == NOBODY.format(org="clinica")


async def test_a_disabled_member_is_told_so_only_once_the_password_matched() -> None:
    orgs, members, ana = await a_clinic()
    org = await orgs.find("clinica")
    assert org is not None
    await members.update(org.id, ana, status="disabled")
    with pytest.raises(NotActive, match="disabled"):
        await sign_in_with_password(
            SigningIn("ana@clinica.uy", PASSWORD), orgs, members, MemoryKeys(), None, SANDBOX
        )
