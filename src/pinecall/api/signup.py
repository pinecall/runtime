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
from pinecall.api.login import the_client
from pinecall.api.members import member_as_json
from pinecall.auth import passwords
from pinecall.types import PRODUCTION, DeclarationRefused, Member, Quotas, a_slug, for_a_person
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
    member = await members.accept(invited.token, hashed)
    assert member is not None
    # Production, and so without `app`: what an admin holds here is every door of the org and not
    # the right to hold an agent from a laptop. `pinecall signup` asks /v1/login/env for the
    # development key next, which is the world its `run` answers in.
    issued = await keys.issue(
        org=org.id,
        label=said.device or SIGNED_UP,
        env=PRODUCTION,
        scopes=for_a_person(member.scopes, PRODUCTION),
        subject=member.id,
        name=member.name,
    )
    minted = codes.mint(issued.record)
    return {
        **issued.as_json,
        "slug": org.slug,
        "member": member_as_json(member),
        "code": minted.code,
        "code_expires_at": minted.expires_at,
    }
