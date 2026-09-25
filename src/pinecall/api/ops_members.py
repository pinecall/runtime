"""The box's doors onto a tenant's people: read them, invite one, remove one, name an operator."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from pinecall.api._deps import KeysDep, MembersDep, OrgsDep, SettingsDep, an_org
from pinecall.api._gateway import where_this_gateway_answers
from pinecall.api._operator import an_operator
from pinecall.api.members import (
    AN_ADMIN,
    INVITED,
    NO_SUCH_MEMBER,
    WantedMember,
    a_wanted_member,
    invited_into,
    member_as_json,
)
from pinecall.api.membership import removed
from pinecall.api.org_mail import OutboxDep
from pinecall.api.orgs import NO_BODY
from pinecall_protocol import WireModel

# The same gate every /v1/ops door takes. The operator may READ an org's people, INVITE one and
# REMOVE one, and may change nobody: an invitation is inert until the person it names accepts it
# with a password of their own, so the box can seat somebody and never be them — while a role
# changed or a member disabled from here would be the box editing a tenant's team. Inviting is
# here because it is how a tenant exists at all on a gateway that takes no sign-up: the operator
# makes the org and invites its first admin (api/signup.py, NOT_HERE). Removing is here because
# the row to remove is sometimes the one the operator's own invitation made by mistake.
operator = APIRouter(prefix="/v1/ops", dependencies=[Depends(an_operator)])


# The operator's invitation takes no seat: a plan caps what an org may seat by ITSELF, and the
# person who runs the box is not somebody the tenant chose to spend a seat on. It is the one door
# through which an org with sign-ups shut gets its first admin.
@operator.post("/orgs/{named}/members", status_code=INVITED)
async def invite_to(
    named: str,
    said: WantedMember,
    orgs: OrgsDep,
    members: MembersDep,
    outbox: OutboxDep,
    settings: SettingsDep,
    request: Request,
) -> dict[str, Any]:
    """The org's first person, or one more: the row, and the one-use token — printed once."""
    org = await an_org(named, orgs)
    role = a_wanted_member(said, org.id)
    base = where_this_gateway_answers(settings, request)
    return await invited_into(members, org, said, role, AN_ADMIN, base, outbox)


class Running(WireModel):
    """Whether this person runs the box. False takes it back, and takes it back at once."""

    operator: bool


# The one write the BOX makes into a tenant's people, and it changes nothing about their org: an
# operator is a person whose own key opens /v1/ops/* as well as their org's doors. It is not part
# of PATCH /v1/members — everything there is the org's to change, on a key with `team`, and this
# one in the same body would be one field away from an org promoting its own admin to run the
# machine it is a tenant on.
@operator.put("/orgs/{named}/members/{id}/operator")
async def runs_the_box(
    named: str, id: str, said: Running, orgs: OrgsDep, members: MembersDep
) -> dict[str, Any]:
    """This person runs this box, or stops. 404 when no member of the org answers to the id."""
    org = await an_org(named, orgs)
    changed = await members.make_operator(org.id, id, said.operator)
    if changed is None:
        raise HTTPException(404, NO_SUCH_MEMBER.format(id=id))
    return member_as_json(changed)


# The operator's twin of DELETE /v1/members/{id}, under the same rules less one: there is no
# "yourself" here, because the box's key is nobody and a person who runs the box is removed from
# THEIR org by this door like anybody else. The last active admin still stays — an org nobody can
# run is as broken when the box made it so.
@operator.delete("/orgs/{named}/members/{id}", status_code=NO_BODY)
async def remove_from(
    named: str, id: str, orgs: OrgsDep, members: MembersDep, keys: KeysDep
) -> None:
    """One person out of the named org for good. 404 for a stranger, 409 for its last admin."""
    org = await an_org(named, orgs)
    await removed(members, keys, org.id, id)


@operator.get("/orgs/{named}/members")
async def of_one_org(named: str, orgs: OrgsDep, members: MembersDep) -> dict[str, Any]:
    """Every member of the named org, oldest first, and how many of them hold a seat."""
    org = await an_org(named, orgs)
    listed = await members.listed(org.id)
    return {
        "members": [member_as_json(member) for member in listed],
        "seated": await members.seated(org.id),
    }
