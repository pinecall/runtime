"""The org's people: invite, list and change them; and the invitation a person accepts."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field
from starlette.status import HTTP_201_CREATED

from pinecall.api.accounts.api_keys import KeyIssued
from pinecall.api.accounts.identity import AtProduction
from pinecall.api.deps import (
    AdmissionDep,
    KeysDep,
    MembersDep,
    OrgsDep,
    SettingsDep,
    TeamKeyDep,
    an_org,
)
from pinecall.api.org.mail import OutboxDep
from pinecall.api.public_url import where_this_gateway_answers
from pinecall.api.scope.grants import elsewhere_too, may_grant
from pinecall.auth import passwords
from pinecall.auth.keys import KeyRecord
from pinecall.auth.members import Members, NoSeatLeft
from pinecall.auth.person_keys import a_persons_key
from pinecall.mail import Letter, Outbox, a_reset, an_invitation, where_the_card_is
from pinecall.types import (
    Member,
    MemberStatus,
    Org,
    Role,
    parse_role,
)
from pinecall_protocol import WireModel

# The doors that make, change or remove a person are production's (api/accounts/identity.py): on a
# sandbox every row is production's, mirrored at a sign-in, and a change made there would be undone
# by the next one. The listing stays on both: it is what the sandbox knows.
router = APIRouter()


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

# Who a letter says invited somebody, when the key that asked names nobody — the box's own, or a
# machine's. The org's name is in the letter beside it, so this reads as what it is.
AN_ADMIN = "An admin"


class WantedMember(WireModel):
    """What an invite says: who, what they will be allowed, and on which agents."""

    email: str
    name: str
    role: str
    # Empty is every agent of the org.
    agents: list[str] = Field(default_factory=list[str])
    # Whether they may act in production. An admin does whatever this says.
    production: bool = False


class Accepting(WireModel):
    """What the person answers the invitation with: the password they chose, and their device."""

    password: str
    # What the first key is labelled — the browser, the laptop.
    device: str | None = None


class MemberSaid(WireModel):
    """One member as the wire says it: the agents sorted, the scopes their role presets beside."""

    id: str
    email: str
    name: str
    role: Role
    agents: list[str]
    status: MemberStatus
    scopes: list[str]
    # Whether they run the BOX, which no role gives and only the box grants. A tenant reading its
    # own team sees it too: somebody who can open every org's door is not a secret from the org
    # they are in.
    operator: bool
    # Whether a request of theirs may run in production: the switch, or being an admin.
    production: bool
    # Whether the address was proved theirs on this row (0048): what lets them be seated in
    # another org without a link, and what a Team screen may say about a pending person.
    verified: bool


class MemberList(WireModel):
    """GET /v1/members: every member of the org, oldest first."""

    members: list[MemberSaid]


class LinkIssued(WireModel):
    """A member and the one-use link minted for them: the token once, or None when the letter
    alone carries it or there was nothing to carry; when it dies; whether a letter was posted."""

    member: MemberSaid
    token: str | None
    expires_at: str | None
    mailed: bool


class FirstKey(KeyIssued):
    """POST /v1/invitations/{token}: the person's first key, once, and the member it names."""

    member: MemberSaid


@router.get("/v1/members")
async def listed(key: TeamKeyDep, members: MembersDep) -> MemberList:
    """Every member of the key's org, oldest first, disabled ones included."""
    return MemberList(members=[a_member_said(member) for member in await members.listed(key.org)])


# 201: the invitation is handed back once — the token is in this answer and hashed everywhere else.
@router.post("/v1/members", status_code=HTTP_201_CREATED, dependencies=[AtProduction])
async def invite(
    said: WantedMember,
    key: TeamKeyDep,
    members: MembersDep,
    admission: AdmissionDep,
    orgs: OrgsDep,
    outbox: OutboxDep,
    settings: SettingsDep,
    request: Request,
) -> LinkIssued:
    """One more person, invited: the row, the one-use token, and the link posted to them."""
    role = a_wanted_member(said, key.org)
    await may_grant(key, members, role, said.production)
    # A seat is charged only where a ROW will be made. An email the org already holds is either a
    # member who accepted — refused below — or one still invited, whose seat was taken when the
    # first invitation went out: re-sending their link must not be the thing an org at its limit
    # cannot do. Judged before the row, because a seat is a stock; 429 and the quota's own
    # sentence, as every other door answers one.
    if await members.by_email(key.org, said.email) is None:
        await admission.a_seat(key.org, await members.seated(key.org))
    org = await an_org(key.org, orgs)
    base = where_this_gateway_answers(settings, request)
    # Handed to this admin only for an address that is this org's alone — and a handed link
    # proves nothing about the address, so only one that travels by mail alone vouches for it.
    alone = not await elsewhere_too(members, key.org, said.email)
    # The write judges the seat again, under its own lock: two invitations at once both passed
    # the count above, and only one of them may make a row (auth/members_postgres.py).
    try:
        return await invited_into(
            members,
            org,
            said,
            role,
            _by(key),
            base,
            outbox,
            handed=alone,
            vouched=not alone,
            seats=(await admission.quotas_of(key.org)).seats,
        )
    except NoSeatLeft as full:
        await admission.a_seat(full.org, full.seated)
        raise  # unreachable: a_seat raised the quota's own sentence, which the handler answers


def a_wanted_member(said: WantedMember, org: str) -> Role:
    """The role the body names, once the shape has refused a bad email or an empty name."""
    role = parse_role(said.role)
    # The shape refuses a bad email or an empty name before any row is made.
    Member(id="m_wanted", org=org, email=said.email, name=said.name, role=role)
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
    seats: int | None = None,
) -> LinkIssued:
    """The row, the token the once, and whether a letter carrying it was posted; 409 for an
    email that already accepted here. `seats` is what the org may hold, judged by the write.

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
        seats=seats,
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
    return LinkIssued(
        member=a_member_said(invited.member),
        token=invited.token if handed else None,
        expires_at=invited.expires_at,
        mailed=await _posted(outbox, org.id, letter),
    )


# A forgotten password handed back by the admin: a one-use link, the token once in this answer and
# mailed to the person where a letter can go, that they open to choose a new password at the very
# door an invitation is accepted at (below). Only an active member is reset — an invited one has
# their invitation, a disabled one is enabled first — and the new link spends every older one. The
# person may also ask for one themselves, where mail can carry it: api/accounts/password_reset.py.
@router.post("/v1/members/{id}/reset", status_code=HTTP_201_CREATED, dependencies=[AtProduction])
async def reset(
    id: str,
    key: TeamKeyDep,
    members: MembersDep,
    orgs: OrgsDep,
    outbox: OutboxDep,
    settings: SettingsDep,
    request: Request,
) -> LinkIssued:
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
    return LinkIssued(
        member=a_member_said(issued.member),
        token=issued.token if handed else None,
        expires_at=issued.expires_at,
        mailed=await _posted(outbox, key.org, letter),
    )


# No key at this door: the person holding the link has none yet. What lets them in is the token,
# one-use and a week old at most; what they leave is a password of their own, hashed; what they
# take away is their first key, minted for them with the scopes their role presets.
@router.post("/v1/invitations/{token}", dependencies=[AtProduction])
async def accept(
    token: str, said: Accepting, members: MembersDep, keys: KeysDep, settings: SettingsDep
) -> FirstKey:
    """Spend the invitation: the member is active, and the answer is their first key, once."""
    kept = await passwords.hashed(said.password, settings.min_password)
    member = await members.accept(token, kept)
    if member is None:
        raise HTTPException(404, NO_INVITATION)
    issued = await a_persons_key(keys, member, said.device or "invitation", settings.world)
    return FirstKey(**issued.as_json, member=a_member_said(member))


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


def a_member_said(member: Member) -> MemberSaid:
    """One member as the wire says it, off their row: sorted where a set would not read the same."""
    return MemberSaid(
        id=member.id,
        email=member.email,
        name=member.name,
        role=member.role,
        agents=sorted(member.agents),
        status=member.status,
        scopes=sorted(member.scopes),
        operator=member.operator,
        production=member.opens_production,
        verified=member.verified,
    )
