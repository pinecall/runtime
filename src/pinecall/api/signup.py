"""POST /v1/signup: a new org on Pinecall's cloud — its first admin, a free trial, the way in."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from pinecall.api._deps import (
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
from pinecall.types import PRODUCTION, DeclarationRefused, Member, Quotas, a_slug
from pinecall_protocol import WireModel

router = APIRouter()

MADE = 201

# A box of its own has an operator who makes orgs (`orgs add`) and invites people; only Pinecall's
# cloud lets a stranger make one. The setting is PINECALL_CLOUD, and /.well-known/pinecall says it.
NOT_HERE = "this gateway takes no sign-ups: it is a box of its own, and its operator invites people"
TAKEN = "{slug} is taken: pick another name for the org"
TOO_MANY = "too many sign-ups from here: try again in a minute"

# What a new org on the cloud may do before anybody pays: the landing page's promise — forty-five
# minutes on us, no card — spelled once, here. A plan later replaces the whole set at
# PUT /v1/ops/orgs/{org}/quotas; the runtime knows no plan, only these numbers.
FREE_TRIAL = Quotas(
    minutes=45,
    messages=500,
    agents=2,
    concurrent_calls=2,
    memory_facts=500,
    knowledge_chunks=2000,
    numbers=1,
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
) -> dict[str, Any]:
    """The org on the free trial, its admin active, their first key, a code for a browser."""
    if not settings.cloud:
        raise HTTPException(403, NOT_HERE)
    if not throttle.allowed(f"{the_client(request)} signup"):
        raise HTTPException(429, TOO_MANY)
    try:
        slug = a_slug(said.org)
        hashed = passwords.hashed(said.password)
        Member(
            id=A_PLACEHOLDER, org=A_PLACEHOLDER, email=said.email, name=said.person, role="admin"
        )
    except DeclarationRefused as refused:
        raise HTTPException(400, str(refused)) from refused
    org = await orgs.create(slug, said.name or said.org)
    if org is None:
        raise HTTPException(409, TAKEN.format(slug=slug))
    await orgs.set_quotas(org.id, FREE_TRIAL)
    # The org is new, so nobody holds the email yet: the invitation is minted and spent in one
    # breath, the very path a person invited later walks, and the member ends `active`.
    invited = await members.invite(org.id, said.email, said.person, "admin", ())
    assert invited is not None
    member = await members.accept(invited.token, hashed)
    assert member is not None
    issued = await keys.issue(
        org=org.id,
        label=said.device or SIGNED_UP,
        env=PRODUCTION,
        scopes=member.scopes,
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
