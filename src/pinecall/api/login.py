"""POST /v1/login: a person's key, minted for them and their device; and the codes that stand in."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from pinecall.api._deps import KeyDep, KeysDep, LoginCodesDep, MembersDep, OrgsDep, ThrottleDep
from pinecall.auth import passwords
from pinecall.types import PRODUCTION, DeclarationRefused, an_env
from pinecall_protocol import WireModel

router = APIRouter()

# One sentence whether the org, the email or the password was wrong: a door that told them apart
# would tell a stranger which orgs and which people exist.
NOBODY = "no member of {org} answers to that email and password"

# The two standings that are not `active`, each with what to do about it. Said only once the
# password matched: a stranger who guessed an email learns nothing about it.
NOT_YET = "{email} has not accepted their invitation yet: open the link and choose a password"
DISABLED = "{email} is disabled in {org}"

# The throttle's own sentence. It counts every try, right or wrong: see auth/throttle.py.
TOO_MANY = "too many attempts for {email}: try again in a minute"

# A code is spent on first use and dies in five minutes: the same answer for every way it is gone.
NO_CODE = "no code answers to that: it was used, it expired, or it never existed"

# A login says one of two things, never both and never neither.
ONE_OR_THE_OTHER = "log in with org, email and password, or with a code — one of the two"

# What a key is labelled when the person named no device: the door it came through.
LOGGED_IN = "login"
A_BROWSER = "console"

# A key opens one world. A person's key may mint the same person's key in the other world — same
# scopes, same subject, same label — because the console's toggle is that person looking the
# other way, not a new right. An org's machine key names nobody and gets nothing here.
ONE_WORLD_EACH = "an org's own key opens one world: issue another with `keys issue --env`"


class OtherWorld(WireModel):
    """Which world the person wants a key for now."""

    env: str


class Login(WireModel):
    """Either a person's org, email and password, or a code somebody's key minted for them."""

    org: str | None = None
    email: str | None = None
    password: str | None = None
    code: str | None = None
    # The world the key opens. A code carries its own — the minting key's — and ignores this.
    env: str = PRODUCTION
    # What the key is labelled: this browser, this laptop. Revoked on its own, later.
    device: str | None = None


# The two ways in share one answer: a key in the clear, once, in the one shape a key travels in.
@router.post("/v1/login")
async def login(
    said: Login,
    request: Request,
    orgs: OrgsDep,
    members: MembersDep,
    keys: KeysDep,
    codes: LoginCodesDep,
    throttle: ThrottleDep,
) -> dict[str, Any]:
    """A key for this person and this device, or a refusal that says the least it can."""
    if said.code is not None:
        if said.org is not None or said.email is not None or said.password is not None:
            raise HTTPException(400, ONE_OR_THE_OTHER)
        return await _with_a_code(said, keys, codes)
    if said.org is None or said.email is None or said.password is None:
        raise HTTPException(400, ONE_OR_THE_OTHER)
    return await _with_a_password(said, _the_client(request), orgs, members, keys, throttle)


# A key holder — `pinecall run`, a person already in — mints a word a browser can carry in a URL
# instead of the key: `https://<gateway>/a/<agent>?login=<code>`. The word stands for the minting
# key's record and is spent for a NEW key with the same org, world, scopes and person, so the
# browser's key is its own and is revoked on its own.
@router.post("/v1/login/codes")
async def a_code(key: KeyDep, codes: LoginCodesDep) -> dict[str, Any]:
    """A one-use code standing for this key's record, good for five minutes."""
    minted = codes.mint(key)
    return {"code": minted.code, "expires_at": minted.expires_at}


@router.post("/v1/login/env")
async def the_other_world(said: OtherWorld, key: KeyDep, keys: KeysDep) -> dict[str, Any]:
    """A key for the same person, with the same scopes, in the world named."""
    if key.subject is None:
        raise HTTPException(403, ONE_WORLD_EACH)
    try:
        env = an_env(said.env)
    except DeclarationRefused as refused:
        raise HTTPException(400, str(refused)) from refused
    issued = await keys.issue(
        org=key.org, label=key.label, env=env, scopes=key.scopes, subject=key.subject, name=key.name
    )
    return issued.as_json


async def _with_a_password(
    said: Login,
    client: str,
    orgs: OrgsDep,
    members: MembersDep,
    keys: KeysDep,
    throttle: ThrottleDep,
) -> dict[str, Any]:
    """The member this org, email and password name, and a key minted for them."""
    assert said.org is not None and said.email is not None and said.password is not None
    try:
        env = an_env(said.env)
    except DeclarationRefused as refused:
        raise HTTPException(400, str(refused)) from refused
    if not throttle.allowed(f"{client} {said.org}/{said.email}"):
        raise HTTPException(429, TOO_MANY.format(email=said.email))
    org = await orgs.find(said.org)
    kept = None if org is None else await members.by_email(org.id, said.email)
    if kept is None or kept.password_hash is None:
        # A member still invited has no hash: to a stranger they do not exist either.
        raise HTTPException(401, NOBODY.format(org=said.org))
    if not passwords.matches(said.password, kept.password_hash):
        raise HTTPException(401, NOBODY.format(org=said.org))
    member = kept.member
    if member.status == "disabled":
        raise HTTPException(403, DISABLED.format(email=member.email, org=said.org))
    if member.status == "invited":
        raise HTTPException(403, NOT_YET.format(email=member.email))
    issued = await keys.issue(
        org=member.org,
        label=said.device or LOGGED_IN,
        env=env,
        scopes=member.scopes,
        subject=member.id,
        name=member.name,
    )
    return issued.as_json


async def _with_a_code(said: Login, keys: KeysDep, codes: LoginCodesDep) -> dict[str, Any]:
    """The record the code stood for, spent, and a key of the browser's own minted from it."""
    assert said.code is not None
    record = codes.spend(said.code)
    if record is None:
        raise HTTPException(404, NO_CODE)
    issued = await keys.issue(
        org=record.org,
        label=said.device or A_BROWSER,
        env=record.env,
        scopes=record.scopes,
        subject=record.subject,
        name=record.name,
    )
    return issued.as_json


def _the_client(request: Request) -> str:
    """Where the knock came from, for the throttle. Unknown is one name, and it is throttled too."""
    return request.client.host if request.client is not None else "unknown"
