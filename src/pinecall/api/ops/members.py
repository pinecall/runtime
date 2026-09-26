"""The box's doors onto a tenant's people: read them, invite one, remove one, name an operator."""

from __future__ import annotations

from fastapi import HTTPException, Request
from starlette.status import HTTP_201_CREATED, HTTP_204_NO_CONTENT

from pinecall.api.accounts.members import (
    AN_ADMIN,
    NO_SUCH_MEMBER,
    LinkIssued,
    MemberSaid,
    WantedMember,
    invited_into,
    parse_member_role,
    wire_member,
)
from pinecall.api.accounts.membership import remove_member
from pinecall.api.deps import KeysDep, MembersDep, OrgsDep, SettingsDep, require_org
from pinecall.api.org.mail import OutboxDep
from pinecall.api.public_url import public_base_url
from pinecall.api.scope.operator_key import operators_router
from pinecall_protocol import WireModel

# The same gate every /v1/ops door takes. The operator may READ an org's people, INVITE one and
# REMOVE one, and may change nobody: an invitation is inert until the person it names accepts it
# with a password of their own, so the box can seat somebody and never be them — while a role
# changed or a member disabled from here would be the box editing a tenant's team. Inviting is here
# because it is how a tenant exists at all on a gateway that takes no sign-up: the operator makes
# the org and invites its first admin (api/accounts/signup.py, NOT_HERE). Removing is here because
# the row to remove is sometimes the one the operator's own invitation made by mistake.
operator = operators_router()


# The operator's invitation takes no seat: a plan caps what an org may seat by ITSELF, and the
# person who runs the box is not somebody the tenant chose to spend a seat on. It is the one door
# through which an org with sign-ups shut gets its first admin.
@operator.post("/orgs/{named}/members", status_code=HTTP_201_CREATED)
async def invite_to(
    named: str,
    said: WantedMember,
    orgs: OrgsDep,
    members: MembersDep,
    outbox: OutboxDep,
    settings: SettingsDep,
    request: Request,
) -> LinkIssued:
    """The org's first person, or one more: the row, and the one-use token — printed once."""
    org = await require_org(named, orgs)
    role = parse_member_role(said, org.id)
    base = public_base_url(settings, request)
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
) -> MemberSaid:
    """This person runs this box, or stops. 404 when no member of the org answers to the id."""
    org = await require_org(named, orgs)
    changed = await members.make_operator(org.id, id, said.operator)
    if changed is None:
        raise HTTPException(404, NO_SUCH_MEMBER.format(id=id))
    return wire_member(changed)


# The operator's twin of DELETE /v1/members/{id}, under the same rules less one: there is no
# "yourself" here, because the box's key is nobody and a person who runs the box is removed from
# THEIR org by this door like anybody else. The last active admin still stays — an org nobody can
# run is as broken when the box made it so.
@operator.delete("/orgs/{named}/members/{id}", status_code=HTTP_204_NO_CONTENT)
async def remove_from(
    named: str, id: str, orgs: OrgsDep, members: MembersDep, keys: KeysDep
) -> None:
    """One person out of the named org for good. 404 for a stranger, 409 for its last admin."""
    org = await require_org(named, orgs)
    await remove_member(members, keys, org.id, id)


class OrgMembers(WireModel):
    """GET /v1/ops/orgs/{org}/members: every member of the org, and how many hold a seat."""

    members: list[MemberSaid]
    seated: int


@operator.get("/orgs/{named}/members")
async def org_members(named: str, orgs: OrgsDep, members: MembersDep) -> OrgMembers:
    """Every member of the named org, oldest first, and how many of them hold a seat."""
    org = await require_org(named, orgs)
    listed = await members.listed(org.id)
    return OrgMembers(
        members=[wire_member(member) for member in listed],
        seated=await members.seated(org.id),
    )
