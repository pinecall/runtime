"""POST /v1/signup: a stranger's org, made only once a code mailed to their address comes back."""

from __future__ import annotations

from hmac import compare_digest
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.status import HTTP_201_CREATED, HTTP_202_ACCEPTED

from pinecall.accounts import OrgFounded, make_org
from pinecall.api.accounts.api_keys import KeyIssued
from pinecall.api.accounts.identity import AtProduction
from pinecall.api.accounts.login import NOBODY_ANYWHERE, throttle_client
from pinecall.api.accounts.members import MemberSaid, wire_member
from pinecall.api.deps import (
    ExtensionsDep,
    KeysDep,
    LoginCodesDep,
    MembersDep,
    OrgsDep,
    SettingsDep,
    SignupsDep,
    ThrottleDep,
)
from pinecall.api.org.mail import OutboxDep
from pinecall.auth import passwords
from pinecall.auth.members import normalize_email
from pinecall.auth.signups import NotVerified, Refusal
from pinecall.mail import signup_code_letter
from pinecall.orgs.records import SLUG_TAKEN, SlugTaken
from pinecall.types import Member, parse_slug
from pinecall_protocol import WireModel

# Production's alone (api/accounts/identity.py): a sandbox keeps no password and makes no person, so
# there every door here is 404, naming where people sign in.
router = APIRouter(dependencies=[AtProduction])

# Off unless the person who runs this gateway turned it on: a box somebody runs for their own
# agents has an operator who makes orgs (`orgs add`) and invites people, and wants no stranger
# making one. The setting is PINECALL_SIGNUP, and /.well-known/pinecall says whether it is on.
NOT_HERE = (
    "this gateway takes no sign-ups: set PINECALL_SIGNUP to open them, or have its operator make "
    "the org and invite you"
)
# A sign-up is proved by a letter, so a box that cannot send one cannot take one.
NO_MAIL = (
    "this gateway cannot mail a code: set PINECALL_SMTP_URL and PINECALL_MAIL_FROM, or have its "
    "operator make the org and invite you"
)
TOO_MANY = "too many sign-ups from here: try again in a minute"
NOT_THE_SHIELD = "the sign-up doors take the key of the page in front of them"

# The address has a row on this box and no password yet: somebody invited it and the person has
# not accepted. A sign-up here would choose that person's password FOR them — one person, one
# password — and the org that invited them would seat whoever typed it at their first login.
ALREADY_INVITED = (
    "{email} was invited to an org on this box: accept that invitation first, then sign up with "
    "the password you chose there"
)

# One sentence per reason, and an email nobody signed up with reads as a wrong code: the door
# never says whether an address has a sign-up waiting.
REFUSED: dict[Refusal, str] = {
    "wrong": "that code is not valid",
    "expired": "that code has expired: ask for a new one",
    "burned": "too many tries: ask for a new code",
}

# The member a sign-up would make, judged before anything is kept: an email with no domain or a
# blank name is refused at the first door, not after the person typed a code.
A_PLACEHOLDER = "pending"


class Signup(WireModel):
    """What a stranger says to make an org: its slug and name, who they are, their password."""

    org: str
    name: str | None = None
    email: str
    person: str
    password: str
    device: str | None = None


class Verifying(WireModel):
    """The code the letter carried, for the address it was sent to."""

    email: str
    code: str
    device: str | None = None


class OrgMade(KeyIssued):
    """POST /v1/signup/verify: the admin's first key, the org's slug, their row, and a code the
    console spends for a key of its own."""

    slug: str
    member: MemberSaid
    code: str
    code_expires_at: float


def wire_org_made(founded: OrgFounded) -> OrgMade:
    """What the door answers of a founded org: the key in the clear, once, and its code beside."""
    return OrgMade(
        **founded.issued.as_json,
        slug=founded.org.slug,
        member=wire_member(founded.admin),
        code=founded.code.code,
        code_expires_at=founded.code.expires_at,
    )


class Resending(WireModel):
    """The address whose sign-up wants a new code."""

    email: str


class CodeMailed(WireModel):
    """POST /v1/signup: the address a code went to, and when the code dies. Never the code."""

    email: str
    code_expires_at: float


class CodeResent(WireModel):
    """POST /v1/signup/resend: nothing, whether or not a sign-up was waiting for the address."""


# Where the knock came from, for the throttle. With PINECALL_SIGNUP_KEY set, only the page that
# holds it gets in — the one running a bot shield in front (pinecall.io checks Pineward) — and
# the person's address is the X-Pinecall-Client it sends, written from what its own proxy saw.
# A header of our own and not X-Forwarded-For, which the proxy in front of this gateway (Caddy)
# strips from a client it does not trust and rewrites as that client's address: the shield's,
# and every person would count as one. Without the key, the header is never believed: anybody
# can write one, and the throttle would be theirs to spread across addresses they made up.
CLIENT_HEADER = "x-pinecall-client"


def signup_client(request: Request, settings: SettingsDep) -> str:
    """The client the throttle counts, once the shield's key was shown when one is set."""
    if settings.signup_key is None:
        return throttle_client(request)
    said = request.headers.get("authorization", "")
    bearer = said.removeprefix("Bearer ").strip() if said.startswith("Bearer ") else ""
    if not bearer or not compare_digest(bearer, settings.signup_key):
        raise HTTPException(401, NOT_THE_SHIELD, headers={"WWW-Authenticate": "Bearer"})
    said_by_the_shield = request.headers.get(CLIENT_HEADER, "").strip()
    return said_by_the_shield or throttle_client(request)


ClientDep = Annotated[str, Depends(signup_client)]


@router.post("/v1/signup", status_code=HTTP_202_ACCEPTED)
async def signup(
    said: Signup,
    client: ClientDep,
    settings: SettingsDep,
    orgs: OrgsDep,
    members: MembersDep,
    signups: SignupsDep,
    throttle: ThrottleDep,
    outbox: OutboxDep,
) -> CodeMailed:
    """The sign-up kept and a code mailed to its address. No org exists until the code is back."""
    if not settings.signup:
        raise HTTPException(403, NOT_HERE)
    if not await outbox.the_box_can_send():
        raise HTTPException(503, NO_MAIL)
    # The address as every row keeps it, so `ANA@x.uy ` is Ana (auth/members.py).
    email = normalize_email(said.email)
    if not throttle.allowed(f"{client} signup"):
        raise HTTPException(429, TOO_MANY)
    slug = parse_slug(said.org)
    hashed = await passwords.hash_password(said.password, settings.min_password)
    Member(id=A_PLACEHOLDER, org=A_PLACEHOLDER, email=email, name=said.person, role="admin")
    # A person who already has a password on this box makes a second org as themselves, and only
    # with THAT password: without this check a signup naming somebody else's email was seated as
    # them, handed a key in their name, and that key minted theirs in every org they belong to
    # (POST /v1/login/org). The refusal is the login's own: it says nothing about who exists.
    known = await members.a_persons_password(email)
    if known is not None and not await passwords.matches(said.password, known):
        raise HTTPException(401, NOBODY_ANYWHERE)
    if known is None and await members.orgs_of(email):
        raise HTTPException(409, ALREADY_INVITED.format(email=email))
    # Refused before a letter goes out; `create` asks again at verify, for a slug taken meanwhile.
    if await orgs.find(slug) is not None:
        raise SlugTaken(SLUG_TAKEN.format(slug=slug))
    pending, code = signups.begin(email, slug, said.name, said.person, hashed, said.device)
    await outbox.post(None, signup_code_letter(email, code, said.person, await outbox.brand()))
    return CodeMailed(email=email, code_expires_at=pending.expires_at)


@router.post("/v1/signup/verify", status_code=HTTP_201_CREATED)
async def verify(
    said: Verifying,
    client: ClientDep,
    settings: SettingsDep,
    orgs: OrgsDep,
    members: MembersDep,
    keys: KeysDep,
    codes: LoginCodesDep,
    signups: SignupsDep,
    throttle: ThrottleDep,
    extensions: ExtensionsDep,
) -> OrgMade:
    """The org made, allowed what its gateway's policy says, its admin active, their first key."""
    if not settings.signup:
        raise HTTPException(403, NOT_HERE)
    if not throttle.allowed(f"{client} signup/verify"):
        raise HTTPException(429, TOO_MANY)
    taken = signups.verify(normalize_email(said.email), said.code.strip())
    if isinstance(taken, NotVerified):
        raise HTTPException(400, REFUSED[taken.reason])
    founded = await make_org(
        taken, said.device, settings.world, orgs, members, keys, codes, extensions
    )
    return wire_org_made(founded)


# The same answer whatever the address: a door that said "nobody signed up as that" would be a
# door that says who did.
@router.post("/v1/signup/resend", status_code=HTTP_202_ACCEPTED)
async def resend(
    said: Resending,
    client: ClientDep,
    settings: SettingsDep,
    signups: SignupsDep,
    throttle: ThrottleDep,
    outbox: OutboxDep,
) -> CodeResent:
    """A new code for a sign-up still waiting, when there is one; the same 202 either way."""
    if not settings.signup:
        raise HTTPException(403, NOT_HERE)
    if not throttle.allowed(f"{client} signup/resend"):
        raise HTTPException(429, TOO_MANY)
    renewed = signups.renewed(normalize_email(said.email))
    if renewed is not None:
        pending, code = renewed
        letter = signup_code_letter(pending.email, code, pending.person, await outbox.brand())
        await outbox.post(None, letter)
    return CodeResent()
