"""A member changed or removed: the role, the agents, the standing, and the last-admin rule."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from starlette.status import HTTP_204_NO_CONTENT

from pinecall.accounts import NO_SUCH_MEMBER, remove_member
from pinecall.api.accounts.identity import AtProduction
from pinecall.api.accounts.members import MemberSaid, wire_member
from pinecall.api.deps import KeysDep, MembersDep, TeamKeyDep
from pinecall.api.scope.grants import may_grant
from pinecall.auth.grants import NOT_YOUR_OWN_ROW
from pinecall.auth.keys import revoked_every_key_of
from pinecall.types import DeclarationRefused, parse_role
from pinecall.types.member import STATUSES, MemberStatus
from pinecall_protocol import WireModel

router = APIRouter()

# `active` is what accepting an invitation makes a person, with a password of their own. An
# update may re-enable a disabled member — they have one — and may not activate an invited one.
NOT_BY_HAND = "{email} has not accepted their invitation: they become active by accepting it"

# Removing is for good, and the person asking cannot remove themselves: somebody else does, which
# is also what proves there is somebody else (the last active admin: accounts/membership.py).
NOT_YOURSELF = "you cannot remove yourself: another admin of this org removes you"
# Disabling is the same act for now, and refused to the person asking for the same reason.
NOT_YOURSELF_DISABLED = "you cannot disable yourself: another admin of this org disables you"
# An admin always opens production (Member.opens_production): the org's owner must reach what
# answers its phone, so taking it from one is refused rather than quietly ignored.
AN_ADMIN_OPENS_PRODUCTION = "{email} is an admin, and an admin always opens production"


class Changed(WireModel):
    """What an update may replace. A field left out keeps what the member had."""

    role: str | None = None
    agents: list[str] | None = None
    status: str | None = None
    production: bool | None = None


@router.patch("/v1/members/{id}", dependencies=[AtProduction])
async def change(
    id: str, said: Changed, key: TeamKeyDep, members: MembersDep, keys: KeysDep
) -> MemberSaid:
    """Replace the role, the agents, the standing or production. Disabling revokes every key of
    theirs, and is refused (409) for the person asking."""
    found = await members.find(key.org, id)
    if found is None:
        raise HTTPException(404, NO_SUCH_MEMBER.format(id=id))
    role = None if said.role is None else parse_role(said.role)
    status = None if said.status is None else parse_member_status(said.status)
    if status == "active" and found.status == "invited":
        raise HTTPException(400, NOT_BY_HAND.format(email=found.email))
    if status == "disabled" and key.subject == id:
        raise HTTPException(409, NOT_YOURSELF_DISABLED)
    # Your own row is not yours to raise: a role and the switch are what another admin gives you.
    if key.subject == id and (role is not None or said.production is not None):
        raise HTTPException(409, NOT_YOUR_OWN_ROW)
    await may_grant(key, members, role, said.production)
    if said.production is False and (role or found.role) == "admin":
        raise HTTPException(409, AN_ADMIN_OPENS_PRODUCTION.format(email=found.email))
    changed = await members.update(
        key.org, id, role=role, agents=said.agents, status=status, production=said.production
    )
    if changed is None:
        raise HTTPException(404, NO_SUCH_MEMBER.format(id=id))
    # A disabled person may not open a door from the next request, and their keys are the doors:
    # the rows stay, revoked, so the log entries that name them stay readable.
    if status == "disabled":
        await revoked_every_key_of(keys, key.org, id)
    return wire_member(changed)


@router.delete("/v1/members/{id}", status_code=HTTP_204_NO_CONTENT, dependencies=[AtProduction])
async def remove(id: str, key: TeamKeyDep, members: MembersDep, keys: KeysDep) -> None:
    """One person out of this org for good. 409 for yourself and for the last active admin."""
    if key.subject == id:
        raise HTTPException(409, NOT_YOURSELF)
    await remove_member(members, keys, key.org, id)


def parse_member_status(word: str) -> MemberStatus:
    """The standing this word names, or a refusal that lists the three."""
    if word not in STATUSES:
        raise DeclarationRefused(f"a member's status is one of {sorted(STATUSES)}, not {word!r}")
    return "invited" if word == "invited" else ("active" if word == "active" else "disabled")
