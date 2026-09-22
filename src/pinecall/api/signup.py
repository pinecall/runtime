"""POST /v1/signup: a new org made by a stranger — its first admin, what it may do, the way in."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from pinecall.api._deps import (
    ExtensionsDep,
    KeysDep,
    LoginCodesDep,
    MembersDep,
    OrgsDep,
    SettingsDep,
    ThrottleDep,
)
from pinecall.api.login import NOBODY_ANYWHERE, the_client
from pinecall.api.members import member_as_json
from pinecall.auth import passwords
from pinecall.auth.members import an_address
from pinecall.auth.persons import a_persons_key
from pinecall.types import DeclarationRefused, Member, Quotas, a_slug
from pinecall_protocol import WireModel

router = APIRouter()

MADE = 201

# Off unless the person who runs this gateway turned it on: a box somebody runs for their own
# agents has an operator who makes orgs (`orgs add`) and invites people, and wants no stranger
# making one. The setting is PINECALL_SIGNUP, and /.well-known/pinecall says whether it is on.
NOT_HERE = (
    "this gateway takes no sign-ups: set PINECALL_SIGNUP to open them, or have its operator make "
    "the org and invite you"
)
TAKEN = "{slug} is taken: pick another name for the org"
TOO_MANY = "too many sign-ups from here: try again in a minute"

# The address has a row on this box and no password yet: somebody invited it and the person has
# not accepted. A sign-up here would choose that person's password FOR them — one person, one
# password — and the org that invited them would seat whoever typed it at their first login.
ALREADY_INVITED = (
    "{email} was invited to an org on this box: accept that invitation first, then sign up with "
    "the password you chose there"
)

# The label of the first key and the seat it names: the door it came through.
SIGNED_UP = "signup"

# The member a sign-up would make, judged before the org row exists: an email with no domain or a
# blank name is refused with nothing created, since a member that fails after `orgs.create` would
# leave an org nobody can enter.
A_PLACEHOLDER = "pending"


class Signup(WireModel):
    """What a stranger says to make an org: its slug and name, who they are, their password."""

    org: str
    name: str | None = None
    email: str
    person: str
    password: str
    device: str | None = None


@router.post("/v1/signup", status_code=MADE)
async def signup(
    said: Signup,
    request: Request,
    settings: SettingsDep,
    orgs: OrgsDep,
    members: MembersDep,
    keys: KeysDep,
    codes: LoginCodesDep,
    throttle: ThrottleDep,
    extensions: ExtensionsDep,
) -> dict[str, Any]:
    """The org made, allowed what its gateway's policy says, its admin active, their first key."""
    if not settings.signup:
        raise HTTPException(403, NOT_HERE)
    # The address as every row keeps it, so `ANA@x.uy ` is Ana (auth/members.py).
    said = said.model_copy(update={"email": an_address(said.email)})
    if not throttle.allowed(f"{the_client(request)} signup"):
        raise HTTPException(429, TOO_MANY)
    try:
        slug = a_slug(said.org)
        hashed = passwords.hashed(said.password, settings.min_password)
        Member(
            id=A_PLACEHOLDER, org=A_PLACEHOLDER, email=said.email, name=said.person, role="admin"
        )
    except DeclarationRefused as refused:
        raise HTTPException(400, str(refused)) from refused
    # A person who already has a password on this box makes a second org as themselves, and only
    # with THAT password: without this check a signup naming somebody else's email was seated as
    # them, handed a key in their name, and that key minted theirs in every org they belong to
    # (POST /v1/login/org). The refusal is the login's own: it says nothing about who exists.
    known = await members.a_persons_password(said.email)
    if known is not None and not passwords.matches(said.password, known):
        raise HTTPException(401, NOBODY_ANYWHERE)
    if known is None and await members.orgs_of(said.email):
        raise HTTPException(409, ALREADY_INVITED.format(email=said.email))
    org = await orgs.create(slug, said.name or said.org)
    if org is None:
        raise HTTPException(409, TAKEN.format(slug=slug))
    # What this org may do is whoever charges for it's to say, through the point a package plugged
    # into (extensions/points.py); the runtime's own answer is no limit, and no limit is no row.
    allowed = extensions.admitted(org, said.email)
    if allowed != Quotas():
        await orgs.set_quotas(org.id, allowed)
    # The org is new, so nobody holds the email yet: the invitation is minted and spent in one
    # breath, the very path a person invited later walks, and the member ends `active`.
    invited = await members.invite(org.id, said.email, said.person, "admin", ())
    assert invited is not None
    # A person who already has a password on this box is seated at once and keeps it: one person,
    # one password (auth/members.py). Anybody else spends the invitation here.
    member = (
        invited.member if invited.token is None else await members.accept(invited.token, hashed)
    )
    assert member is not None
    # The admin's own key, which opens production too: an admin always does (0039).
    issued = await a_persons_key(keys, member, said.device or SIGNED_UP)
    minted = codes.mint(issued.record)
    return {
        **issued.as_json,
        "slug": org.slug,
        "member": member_as_json(member),
        "code": minted.code,
        "code_expires_at": minted.expires_at,
    }
