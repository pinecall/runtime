"""The org's people: listed, invited, changed, reset, removed."""

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Path, Request

from pinecall.domain.errors import Conflict, DeclarationRefused, NotFound
from pinecall.domain.names import AN_ADDRESS, PRODUCTION
from pinecall.domain.person import parse_role, parse_status
from pinecall.gateway._deps import Acting, GatewayDep, TeamKey, public_url
from pinecall.gateway._gateway import Gateway
from pinecall.tenancy import admission, keys, letters, orgs, people
from pinecall.tenancy.letters import Brand, Letter, Link
from pinecall.wire.rest.accounts import (
    ChangeMemberRequest,
    InvitationResponse,
    InviteMemberRequest,
    MemberList,
    MemberRow,
)

router = APIRouter()


# Invited people become active by accepting; a change may only wake a disabled one.
NOT_BY_HAND = "{email} has not accepted their invitation: they become active by accepting it"


NOT_YOURSELF = "you cannot remove yourself: another admin of this org removes you"


NOT_YOURSELF_DISABLED = "you cannot disable yourself: another admin of this org disables you"


NOT_YOUR_OWN_ROW = "nobody changes their own role or production access: another admin does"


AN_ADMIN_OPENS_PRODUCTION = "{email} is an admin, and an admin always opens production"


NOT_ACTIVE = (
    "{email} is {status}, not active: an invited member uses their invitation, and a disabled one "
    "is enabled before their password is reset"
)


NOT_AN_ADDRESS = "an email has one @ and a domain, not {email!r}"


# Who a letter says invited or reset, when the key names nobody.
AN_ADMIN = "An admin"


type Worded = Callable[[str, Link, Brand], Letter]


# The path says {id}, as the console spells it; `id` is a builtin, so the function says member.
MemberId = Annotated[str, Path(alias="id")]


A_MEMBER = "/v1/members/{id}"


A_MEMBERS_RESET = "/v1/members/{id}/reset"


@router.get("/v1/members")
async def list_members(key: TeamKey, gateway: GatewayDep) -> MemberList:
    """Every member of the org, oldest first, the disabled ones too."""
    listed = await people.listed(gateway.connections.pool, key.org)
    return MemberList(members=[MemberRow.of(member) for member in listed])


# The token is answered this once and kept as a hash. It is handed to the admin only when the
# address is in no other org: the link sets the person's one password, so it is otherwise mailed
# only, and then it proves the address.
@router.post("/v1/members", status_code=201)
async def invite_member(
    body: InviteMemberRequest, key: TeamKey, request: Request, gateway: GatewayDep
) -> InvitationResponse:
    """Invite a person, within the org's seats, and mail them the link."""
    pool = gateway.connections.pool
    role = parse_role(body.role)
    if not AN_ADDRESS.match(body.email.strip()):
        raise DeclarationRefused(NOT_AN_ADDRESS.format(email=body.email))
    keys.check_may_grant(key.bearer, role, production=body.production)
    elsewhere = await _member_elsewhere(gateway, key.org, body.email)
    seats = (await admission.quotas_of(pool, key.org, PRODUCTION)).seats
    invitee = people.Invitee(body.email, body.name, role, frozenset(body.agents), body.production)
    invited = await people.invite(pool, key.org, invitee, seats=seats, vouched=elsewhere)
    mailed = await _mailed(
        request, gateway, key=key, invited=invited, worded=letters.invitation_letter
    )
    return InvitationResponse(
        member=MemberRow.of(invited.member),
        token=None if elsewhere else invited.token,
        expires_at=invited.expires_at,
        mailed=mailed,
        link=_card(request, gateway, None if elsewhere else invited.token),
    )


@router.patch(A_MEMBER)
async def change_member(
    member: MemberId, body: ChangeMemberRequest, key: TeamKey, gateway: GatewayDep
) -> MemberRow:
    """Change a member's role, agents, standing or production access; disabling revokes keys."""
    pool = gateway.connections.pool
    found = await people.find(pool, key.org, member)
    if found is None:
        raise NotFound(people.NOBODY_BY_THAT_ID)
    role = None if body.role is None else parse_role(body.role)
    status = None if body.status is None else parse_status(body.status)
    if status == "active" and found.status == "invited":
        raise DeclarationRefused(NOT_BY_HAND.format(email=found.email))
    yours = key.bearer.key.subject == member
    if status == "disabled" and yours:
        raise Conflict(NOT_YOURSELF_DISABLED)
    if yours and (role is not None or body.production is not None):
        raise Conflict(NOT_YOUR_OWN_ROW)
    keys.check_may_grant(key.bearer, role, production=bool(body.production))
    if body.production is False and (role or found.role) == "admin":
        raise Conflict(AN_ADMIN_OPENS_PRODUCTION.format(email=found.email))
    agents = None if body.agents is None else frozenset(body.agents)
    change = people.Change(role=role, agents=agents, status=status, production=body.production)
    return MemberRow.of(await people.update(pool, key.org, member, change))


@router.delete(A_MEMBER, status_code=204)
async def remove_member(member: MemberId, key: TeamKey, gateway: GatewayDep) -> None:
    """Take a member out for good, their keys revoked; never yourself, never the last admin."""
    if key.bearer.key.subject == member:
        raise Conflict(NOT_YOURSELF)
    await people.remove(gateway.connections.pool, key.org, member)


# A new link spends the older ones; it is handed over on the same terms as an invitation.
@router.post(A_MEMBERS_RESET, status_code=201)
async def reset_password(
    member: MemberId, key: TeamKey, request: Request, gateway: GatewayDep
) -> InvitationResponse:
    """A one-use link that sets an active member's password, mailed to them."""
    pool = gateway.connections.pool
    found = await people.find(pool, key.org, member)
    if found is None:
        raise NotFound(people.NOBODY_BY_THAT_ID)
    elsewhere = await _member_elsewhere(gateway, key.org, found.email)
    issued = await people.reset(pool, key.org, member, vouched=elsewhere)
    if issued is None:
        raise Conflict(NOT_ACTIVE.format(email=found.email, status=found.status))
    mailed = await _mailed(request, gateway, key=key, invited=issued, worded=letters.reset_letter)
    return InvitationResponse(
        member=MemberRow.of(issued.member),
        token=None if elsewhere else issued.token,
        expires_at=issued.expires_at,
        mailed=mailed,
        link=_card(request, gateway, None if elsewhere else issued.token),
    )


def _card(request: Request, gateway: Gateway, token: str | None) -> str | None:
    return None if token is None else letters.card_link(public_url(request, gateway), token)


async def _member_elsewhere(gateway: Gateway, org: str, email: str) -> bool:
    rows = await people.orgs_of(gateway.connections.pool, email)
    return any(row.org != org for row in rows)


async def _mailed(
    request: Request, gateway: Gateway, *, key: Acting, invited: people.Invited, worded: Worded
) -> bool:
    if invited.token is None:
        return False
    pool = gateway.connections.pool
    org = await orgs.find(pool, key.org)
    link = Link(
        org=key.org if org is None else org.name,
        link=letters.card_link(public_url(request, gateway), invited.token),
        by=key.bearer.key.name or AN_ADMIN,
        dies=invited.expires_at,
    )
    letter = worded(invited.member.email, link, await letters.brand_of(pool))
    return await gateway.outbox.post(key.org, letter)
