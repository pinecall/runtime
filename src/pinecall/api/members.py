"""The org's people: invite, list and change them; and the invitation a person accepts."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from pinecall.api._deps import (
    AdmissionDep,
    KeysDep,
    MembersDep,
    OrgsDep,
    SettingsDep,
    TeamKeyDep,
    an_org,
)
from pinecall.api._gateway import where_this_gateway_answers
from pinecall.api._seating import elsewhere_too, may_grant
from pinecall.api.identity import AtProduction
from pinecall.api.org_mail import OutboxDep
from pinecall.api.orgs import NO_BODY
from pinecall.auth import passwords
from pinecall.auth.granting import NOT_YOUR_OWN_ROW
from pinecall.auth.keys import KeyRecord, Keys, revoked_every_key_of
from pinecall.auth.members import Members
from pinecall.auth.persons import a_persons_key
from pinecall.mail import Letter, Outbox, a_reset, an_invitation, where_the_card_is
from pinecall.orgs.admission import QuotaExhausted
from pinecall.types import (
    DeclarationRefused,
    Member,
    Org,
    Role,
    a_role,
)
from pinecall.types.member import STATUSES, MemberStatus
from pinecall_protocol import WireModel

# The doors that make, change or remove a person are production's (api/identity.py): on a sandbox
# every row is production's, mirrored at a sign-in, and a change made there would be undone by the
# next one. The listing stays on both: it is what the sandbox knows.
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

NOT_ACTIVE = (
    "{email} is {status}, not active: an invited member uses their invitation, and a disabled one "
    "is enabled before their password is reset"
)

# `active` is what accepting an invitation makes a person, with a password of their own. An
# update may re-enable a disabled member — they have one — and may not activate an invited one.
NOT_BY_HAND = "{email} has not accepted their invitation: they become active by accepting it"

# Removing is for good, so the two that would leave an org nobody can run are refused in a
# sentence: the person asking cannot remove themselves — somebody else does, which is also what
# proves there is somebody else — and the last ACTIVE admin stays until another one exists. An
# invited admin does not count: an org whose only admin has not chosen a password is an org
# nobody can sign in to.
NOT_YOURSELF = "you cannot remove yourself: another admin of this org removes you"
# Disabling is the same act for now, and refused to the person asking for the same reason.
NOT_YOURSELF_DISABLED = "you cannot disable yourself: another admin of this org disables you"
# An admin always opens production (Member.opens_production): the org's owner must reach what
# answers its phone, so taking it from one is refused rather than quietly ignored.
AN_ADMIN_OPENS_PRODUCTION = "{email} is an admin, and an admin always opens production"
THE_LAST_ADMIN = (
    "{email} is the last active admin of this org: make somebody else an admin first, "
    "or the org is left with nobody who can run it"
)

# Who a letter says invited somebody, when the key that asked names nobody — the box's own, or a
# machine's. The org's name is in the letter beside it, so this reads as what it is.
AN_ADMIN = "An admin"


class WantedMember(WireModel):
    """What an invite says: who, what they will be allowed, and on which agents."""

    email: str
    name: str
    role: str
    # Empty is every agent of the org.
    agents: list[str] = []
    # Whether they may act in production. An admin does whatever this says.
    production: bool = False


class Changed(WireModel):
    """What an update may replace. A field left out keeps what the member had."""

    role: str | None = None
    agents: list[str] | None = None
    status: str | None = None
    production: bool | None = None


class Accepting(WireModel):
    """What the person answers the invitation with: the password they chose, and their device."""

    password: str
    # What the first key is labelled — the browser, the laptop.
    device: str | None = None


@router.get("/v1/members")
async def listed(key: TeamKeyDep, members: MembersDep) -> dict[str, Any]:
    """Every member of the key's org, oldest first, disabled ones included."""
    return {"members": [member_as_json(member) for member in await members.listed(key.org)]}


@router.post("/v1/members", status_code=INVITED, dependencies=[AtProduction])
async def invite(
    said: WantedMember,
    key: TeamKeyDep,
    members: MembersDep,
    admission: AdmissionDep,
    orgs: OrgsDep,
    outbox: OutboxDep,
    settings: SettingsDep,
    request: Request,
) -> dict[str, Any]:
    """One more person, invited: the row, the one-use token, and the link posted to them."""
    role = a_wanted_member(said, key.org)
    await may_grant(key, members, role, said.production)
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
    org = await an_org(key.org, orgs)
    base = where_this_gateway_answers(settings, request)
    # Handed to this admin only for an address that is this org's alone — and a handed link
    # proves nothing about the address, so only one that travels by mail alone vouches for it.
    alone = not await elsewhere_too(members, key.org, said.email)
    return await invited_into(
        members, org, said, role, _by(key), base, outbox, handed=alone, vouched=not alone
    )


def a_wanted_member(said: WantedMember, org: str) -> Role:
    """The role the body names, once the shape has refused a bad email or an empty name."""
    try:
        role = a_role(said.role)
        # The shape refuses a bad email or an empty name before any row is made.
        Member(id="m_wanted", org=org, email=said.email, name=said.name, role=role)
    except DeclarationRefused as refused:
        raise HTTPException(400, str(refused)) from refused
    return role


async def invited_into(
    members: Members,
    org: Org,
    said: WantedMember,
    role: Role,
    inviter: str,
    base: str,
    outbox: Outbox,
    *,
    handed: bool = True,
    vouched: bool = True,
) -> dict[str, Any]:
    """The row, the token the once, and whether a letter carrying it was posted; 409 for an
    email that already accepted here.

    A person who already exists on this box — an email with a password in another org, and
    proved to be theirs — is seated active at once and the answer carries no token: they sign
    in with the password they have, and the console's org switch lists the new org beside the
    others. There is nothing for a letter to carry, so nothing is posted and `mailed` is false.

    `handed` false keeps the token out of the answer too: the letter carries it, and only the
    letter (`elsewhere_too`). `vouched` says whether accepting it proves the address (0048).
    The box's own door hands it over always, and vouches: the operator knows who they seat.
    """
    invited = await members.invite(
        org.id,
        said.email,
        said.name,
        role,
        said.agents,
        production=said.production,
        vouched=vouched,
    )
    if invited is None:
        raise HTTPException(409, ALREADY_A_MEMBER.format(email=said.email))
    letter = (
        None
        if invited.token is None
        else an_invitation(
            invited.member.email,
            org.name,
            inviter,
            where_the_card_is(base, invited.token),
            invited.expires_at,
        )
    )
    return {
        "member": member_as_json(invited.member),
        "token": invited.token if handed else None,
        "expires_at": invited.expires_at,
        "mailed": await _posted(outbox, org.id, letter),
    }


@router.patch("/v1/members/{id}", dependencies=[AtProduction])
async def change(
    id: str, said: Changed, key: TeamKeyDep, members: MembersDep, keys: KeysDep
) -> dict[str, Any]:
    """Replace the role, the agents, the standing or production. Disabling revokes every key of
    theirs, and is refused (409) for the person asking."""
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
    return member_as_json(changed)


# For good, where `disabled` is for now: the keys stop first, so there is no moment at which the
# row is gone and a key of theirs still opens a door; then the row goes, its open links with it
# (0014's CASCADE), and the seat is free because a seat is a count of rows. What the log wrote
# about them stays readable — it names the id as text, and the id now names nobody.
@router.delete("/v1/members/{id}", status_code=NO_BODY, dependencies=[AtProduction])
async def remove(id: str, key: TeamKeyDep, members: MembersDep, keys: KeysDep) -> None:
    """One person out of this org for good. 409 for yourself and for the last active admin."""
    if key.subject == id:
        raise HTTPException(409, NOT_YOURSELF)
    await removed(members, keys, key.org, id)


async def removed(members: Members, keys: Keys, org: str, id: str) -> None:
    """Every key of theirs revoked, then the row and its links gone; 404, or 409 for the last
    active admin. The org's door and the operator's twin both end here."""
    found = await members.find(org, id)
    if found is None:
        raise HTTPException(404, NO_SUCH_MEMBER.format(id=id))
    if _is_an_active_admin(found) and not any(
        _is_an_active_admin(other) and other.id != id for other in await members.listed(org)
    ):
        raise HTTPException(409, THE_LAST_ADMIN.format(email=found.email))
    await revoked_every_key_of(keys, org, id)
    if not await members.remove(org, id):
        raise HTTPException(404, NO_SUCH_MEMBER.format(id=id))


def _is_an_active_admin(member: Member) -> bool:
    """Whether this person can run the org today: an admin who has chosen a password."""
    return member.role == "admin" and member.status == "active"


# A forgotten password handed back by the admin: a one-use link, the token once in this answer and
# mailed to the person where a letter can go, that they open to choose a new password at the very
# door an invitation is accepted at (below). Only an active member is reset — an invited one has
# their invitation, a disabled one is enabled first — and the new link spends every older one.
# The person may also ask for one themselves, where mail can carry it: api/forgot.py.
@router.post("/v1/members/{id}/reset", status_code=INVITED, dependencies=[AtProduction])
async def reset(
    id: str,
    key: TeamKeyDep,
    members: MembersDep,
    orgs: OrgsDep,
    outbox: OutboxDep,
    settings: SettingsDep,
    request: Request,
) -> dict[str, Any]:
    """A one-use link that sets this member's password; 409 for a member who is not active."""
    found = await members.find(key.org, id)
    if found is None:
        raise HTTPException(404, NO_SUCH_MEMBER.format(id=id))
    # The link sets the person's ONE password, in every org of theirs: handed to this admin only
    # when the person is this org's alone, posted to the person otherwise (`elsewhere_too`) —
    # and only a link that travels by mail alone proves the address it reaches.
    handed = not await elsewhere_too(members, key.org, found.email)
    issued = await members.reset(key.org, id, vouched=not handed)
    if issued is None:
        raise HTTPException(409, NOT_ACTIVE.format(email=found.email, status=found.status))
    org = await an_org(key.org, orgs)
    link = where_the_card_is(where_this_gateway_answers(settings, request), issued.token or "")
    letter = (
        None
        if issued.token is None
        else a_reset(found.email, org.name, _by(key), link, issued.expires_at)
    )
    return {
        "member": member_as_json(issued.member),
        "token": issued.token if handed else None,
        "expires_at": issued.expires_at,
        "mailed": await _posted(outbox, key.org, letter),
    }


# No key at this door: the person holding the link has none yet. What lets them in is the token,
# one-use and a week old at most; what they leave is a password of their own, hashed; what they
# take away is their first key, minted for them with the scopes their role presets.
@router.post("/v1/invitations/{token}", dependencies=[AtProduction])
async def accept(
    token: str, said: Accepting, members: MembersDep, keys: KeysDep, settings: SettingsDep
) -> dict[str, Any]:
    """Spend the invitation: the member is active, and the answer is their first key, once."""
    try:
        kept = passwords.hashed(said.password, settings.min_password)
    except DeclarationRefused as refused:
        raise HTTPException(400, str(refused)) from refused
    member = await members.accept(token, kept)
    if member is None:
        raise HTTPException(404, NO_INVITATION)
    issued = await a_persons_key(keys, member, said.device or "invitation", settings.world)
    return {**issued.as_json, "member": member_as_json(member)}


# `mailed` says a letter was HANDED OVER to a mail server, never that it arrived: the send runs
# after this door has answered (mail/outbox.py), because a door blocked on somebody else's relay
# is a door. False is the honest answer for a box and an org that both send no mail at all, and
# it is what every answer carried before this existed.
async def _posted(outbox: Outbox, org: str, letter: Letter | None) -> bool:
    """Whether there was a letter to post and somebody to post it through."""
    return False if letter is None else await outbox.post(org, letter)


def _by(key: KeyRecord) -> str:
    """Whose name a letter says invited or reset somebody: the person, or a machine's `An admin`."""
    return key.name or AN_ADMIN


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
        # Whether a request of theirs may run in production: the switch, or being an admin.
        "production": member.opens_production,
        # Whether the address was proved theirs on this row (0048): what lets them be seated in
        # another org without a link, and what a Team screen may say about a pending person.
        "verified": member.verified,
    }
