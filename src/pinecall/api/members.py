"""The org's people: invite, list and change them; and the invitation a person accepts."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from pinecall.api._deps import (
    AdmissionDep,
    KeysDep,
    MembersDep,
    OrgsDep,
    SettingsDep,
    TeamKeyDep,
    an_org,
)
from pinecall.api._operator import an_operator
from pinecall.auth import passwords
from pinecall.auth.keys import Keys
from pinecall.auth.members import Members
from pinecall.orgs.admission import QuotaExhausted
from pinecall.types import (
    PRODUCTION,
    DeclarationRefused,
    Member,
    Role,
    a_role,
    an_env,
    for_a_person,
)
from pinecall.types.member import STATUSES, MemberStatus
from pinecall_protocol import WireModel

router = APIRouter()

# The same gate every /v1/ops door takes. The operator may READ an org's people and INVITE one,
# and may change nobody: an invitation is inert until the person it names accepts it with a
# password of their own, so the box can seat somebody and never be them — while a role changed
# or a member disabled from here would be the box editing a tenant's team. Inviting is here
# because it is how a tenant exists at all on a gateway that takes no sign-up: the operator makes
# the org and invites its first admin (api/signup.py, NOT_HERE).
operator = APIRouter(prefix="/v1/ops", dependencies=[Depends(an_operator)])

# The invitation is handed back once: the token is in this answer and hashed everywhere else.
INVITED = 201

# The email already belongs to somebody who accepted: they log in, and nobody re-invites them.
ALREADY_A_MEMBER = "{email} is already a member of this org: they log in"

# Nothing answers to that token: it was used, it expired after its week, or it never existed.
# One sentence for the three, because the person holding a dead link can act the same on each.
NO_INVITATION = (
    "no open invitation answers to that token: it was used, it expired, or it never existed"
)

NO_SUCH_MEMBER = "no member {id} in this org"

# `active` is what accepting an invitation makes a person, with a password of their own. An
# update may re-enable a disabled member — they have one — and may not activate an invited one.
NOT_BY_HAND = "{email} has not accepted their invitation: they become active by accepting it"


class WantedMember(WireModel):
    """What an invite says: who, what they will be allowed, and on which agents."""

    email: str
    name: str
    role: str
    # Empty is every agent of the org.
    agents: list[str] = []


class Changed(WireModel):
    """What an update may replace. A field left out keeps what the member had."""

    role: str | None = None
    agents: list[str] | None = None
    status: str | None = None


class Accepting(WireModel):
    """What the person answers the invitation with: the password they chose, and their device."""

    password: str
    # The world the first key opens, and what the key is labelled — the browser, the laptop.
    env: str = PRODUCTION
    device: str | None = None


@router.get("/v1/members")
async def listed(key: TeamKeyDep, members: MembersDep) -> dict[str, Any]:
    """Every member of the key's org, oldest first, disabled ones included."""
    return {"members": [member_as_json(member) for member in await members.listed(key.org)]}


@router.post("/v1/members", status_code=INVITED)
async def invite(
    said: WantedMember, key: TeamKeyDep, members: MembersDep, admission: AdmissionDep
) -> dict[str, Any]:
    """One more person, invited: the row, and the one-use token that makes them a member."""
    role = _a_wanted_member(said, key.org)
    # A seat is charged only where a ROW will be made. An email the org already holds is either a
    # member who accepted — refused below — or one still invited, whose seat was taken when the
    # first invitation went out: re-sending their link must not be the thing an org at its limit
    # cannot do. Judged before the row, because a seat is a stock; 429 and the quota's own
    # sentence, as every other door answers one.
    if await members.by_email(key.org, said.email) is None:
        try:
            await admission.a_seat(key.org, await members.seated(key.org))
        except QuotaExhausted as refused:
            raise HTTPException(429, str(refused)) from refused
    return await _invited(members, key.org, said, role)


# The operator's invitation takes no seat: a plan caps what an org may seat by ITSELF, and the
# person who runs the box is not somebody the tenant chose to spend a seat on. It is the one door
# through which an org with sign-ups shut gets its first admin.
@operator.post("/orgs/{named}/members", status_code=INVITED)
async def invite_to(
    named: str, said: WantedMember, orgs: OrgsDep, members: MembersDep
) -> dict[str, Any]:
    """The org's first person, or one more: the row, and the one-use token — printed once."""
    org = await an_org(named, orgs)
    role = _a_wanted_member(said, org.id)
    return await _invited(members, org.id, said, role)


def _a_wanted_member(said: WantedMember, org: str) -> Role:
    """The role the body names, once the shape has refused a bad email or an empty name."""
    try:
        role = a_role(said.role)
        # The shape refuses a bad email or an empty name before any row is made.
        Member(id="m_wanted", org=org, email=said.email, name=said.name, role=role)
    except DeclarationRefused as refused:
        raise HTTPException(400, str(refused)) from refused
    return role


async def _invited(members: Members, org: str, said: WantedMember, role: Role) -> dict[str, Any]:
    """The row and the token, the once; 409 for an email that already accepted."""
    invited = await members.invite(org, said.email, said.name, role, said.agents)
    if invited is None:
        raise HTTPException(409, ALREADY_A_MEMBER.format(email=said.email))
    return {
        "member": member_as_json(invited.member),
        "token": invited.token,
        "expires_at": invited.expires_at,
    }


@router.patch("/v1/members/{id}")
async def change(
    id: str, said: Changed, key: TeamKeyDep, members: MembersDep, keys: KeysDep
) -> dict[str, Any]:
    """Replace the role, the agents or the standing. Disabling revokes every key of theirs."""
    found = await members.find(key.org, id)
    if found is None:
        raise HTTPException(404, NO_SUCH_MEMBER.format(id=id))
    try:
        role = None if said.role is None else a_role(said.role)
        status = None if said.status is None else _a_status(said.status)
    except DeclarationRefused as refused:
        raise HTTPException(400, str(refused)) from refused
    if status == "active" and found.status == "invited":
        raise HTTPException(400, NOT_BY_HAND.format(email=found.email))
    changed = await members.update(key.org, id, role=role, agents=said.agents, status=status)
    if changed is None:
        raise HTTPException(404, NO_SUCH_MEMBER.format(id=id))
    # A disabled person may not open a door from the next request, and their keys are the doors:
    # the rows stay, revoked, so the log entries that name them stay readable.
    if status == "disabled":
        await _revoked_every_key_of(keys, key.org, id)
    return member_as_json(changed)


# No key at this door: the person holding the link has none yet. What lets them in is the token,
# one-use and a week old at most; what they leave is a password of their own, hashed; what they
# take away is their first key, minted for them with the scopes their role presets.
@router.post("/v1/invitations/{token}")
async def accept(
    token: str, said: Accepting, members: MembersDep, keys: KeysDep, settings: SettingsDep
) -> dict[str, Any]:
    """Spend the invitation: the member is active, and the answer is their first key, once."""
    try:
        env = an_env(said.env)
        kept = passwords.hashed(said.password, settings.min_password)
    except DeclarationRefused as refused:
        raise HTTPException(400, str(refused)) from refused
    member = await members.accept(token, kept)
    if member is None:
        raise HTTPException(404, NO_INVITATION)
    issued = await keys.issue(
        org=member.org,
        label=said.device or "invitation",
        env=env,
        scopes=for_a_person(member.scopes, env),
        subject=member.id,
        name=member.name,
    )
    return {**issued.as_json, "member": member_as_json(member)}


async def _revoked_every_key_of(keys: Keys, org: str, member: str) -> None:
    """Every live key minted for this person, stopped. The org's machine keys are not theirs."""
    for row in await keys.listed(org):
        if row.subject == member and row.revoked_at is None:
            await keys.revoke(row.fingerprint)


def _a_status(word: str) -> MemberStatus:
    """The standing this word names, or a refusal that lists the three."""
    if word not in STATUSES:
        raise DeclarationRefused(f"a member's status is one of {sorted(STATUSES)}, not {word!r}")
    return "invited" if word == "invited" else ("active" if word == "active" else "disabled")


def member_as_json(member: Member) -> dict[str, Any]:
    """One member as the wire says it: the agents sorted, the scopes their role presets beside."""
    return {
        "id": member.id,
        "email": member.email,
        "name": member.name,
        "role": member.role,
        "agents": sorted(member.agents),
        "status": member.status,
        "scopes": sorted(member.scopes),
        # Whether they run the BOX, which no role gives and only the box grants. A tenant reading
        # its own team sees it too: somebody who can open every org's door is not a secret from
        # the org they are in.
        "operator": member.operator,
    }


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


@operator.get("/orgs/{named}/members")
async def of_one_org(named: str, orgs: OrgsDep, members: MembersDep) -> dict[str, Any]:
    """Every member of the named org, oldest first, and how many of them hold a seat."""
    org = await an_org(named, orgs)
    listed = await members.listed(org.id)
    return {
        "members": [member_as_json(member) for member in listed],
        "seated": await members.seated(org.id),
    }
