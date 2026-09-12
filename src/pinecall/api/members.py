"""The org's people: invite, list and change them; and the invitation a person accepts."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from pinecall.api._deps import AdmissionDep, KeysDep, MembersDep, TeamKeyDep
from pinecall.auth import passwords
from pinecall.auth.keys import Keys
from pinecall.orgs.admission import QuotaExhausted
from pinecall.types import PRODUCTION, DeclarationRefused, Member, a_role, an_env, for_a_person
from pinecall.types.member import STATUSES, MemberStatus
from pinecall_protocol import WireModel

router = APIRouter()

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
    try:
        role = a_role(said.role)
        # The shape refuses a bad email or an empty name before any row is made.
        Member(id="m_wanted", org=key.org, email=said.email, name=said.name, role=role)
    except DeclarationRefused as refused:
        raise HTTPException(400, str(refused)) from refused
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
    invited = await members.invite(key.org, said.email, said.name, role, said.agents)
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
async def accept(token: str, said: Accepting, members: MembersDep, keys: KeysDep) -> dict[str, Any]:
    """Spend the invitation: the member is active, and the answer is their first key, once."""
    try:
        env = an_env(said.env)
        kept = passwords.hashed(said.password)
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
    }
