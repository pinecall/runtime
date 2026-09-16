"""POST /v1/login: a person's key, minted for them and their device; and the codes that stand in."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from pinecall.api._deps import KeyDep, KeysDep, LoginCodesDep, MembersDep, OrgsDep, ThrottleDep
from pinecall.auth import passwords
from pinecall.auth.keys import KeyRecord
from pinecall.auth.members import Kept
from pinecall.types import PRODUCTION, DeclarationRefused, Env, Member, an_env, for_a_person
from pinecall_protocol import WireModel

router = APIRouter()

# One sentence whether the org, the email or the password was wrong: a door that told them apart
# would tell a stranger which orgs and which people exist.
NOBODY = "no member of {org} answers to that email and password"
# The same sentence when no org was named: a person is their email on this box, and a login
# with no org lands in the oldest org they belong to.
NOBODY_ANYWHERE = "nobody answers to that email and password"

# A machine key names no person, so it belongs to one org and has no other to switch to.
ONE_ORG_EACH = "an org's own key names nobody: it opens one org"

# The person is not a member of the org they asked to switch to. Said only to a person.
NOT_THERE = "you are not an active member of {org}"

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

# A key opens one world. A person's key may mint the same person's key in the other world —
# same person, same label, the scopes their role presets there — because the console's toggle is
# that person looking the other way, not a new right. An org's machine key names nobody and gets
# nothing here.
ONE_WORLD_EACH = "an org's own key opens one world: issue another with `keys issue --env`"

# The key names a member the table no longer has an active row for: they were removed, or
# disabled while holding a key. Their key still opens its own world until it is revoked; it does
# not open a second one.
NOT_A_MEMBER = "the person this key was minted for is no longer an active member of this org"


class OtherWorld(WireModel):
    """Which world the person wants a key for now."""

    env: str


class OtherOrg(WireModel):
    """Which of the person's orgs they want a key for now."""

    org: str


class Login(WireModel):
    """A person's email and password, in one of their orgs; or a code somebody's key minted."""

    # Left out, the oldest org the person belongs to: a person with one org never types it.
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
    if said.email is None or said.password is None:
        raise HTTPException(400, ONE_OR_THE_OTHER)
    return await _with_a_password(said, the_client(request), orgs, members, keys, throttle)


# A person is their email on this box, and may belong to several orgs: this lists them for the
# console's org switch, and the door below mints the same person's key in the one they pick.
@router.get("/v1/login/orgs")
async def the_persons_orgs(key: KeyDep, orgs: OrgsDep, members: MembersDep) -> dict[str, Any]:
    """Every org this key's person belongs to, oldest first, and which one this key opens."""
    person = await _the_person(key, members)
    listed: list[dict[str, Any]] = []
    for row in await members.orgs_of(person.email):
        if row.status == "disabled":
            continue
        org = await orgs.find(row.org)
        listed.append(
            {
                "org": row.org,
                "slug": None if org is None else org.slug,
                "name": None if org is None else org.name,
                "role": row.role,
                "status": row.status,
                "here": row.org == key.org,
            }
        )
    return {"orgs": listed}


@router.post("/v1/login/org")
async def the_other_org(
    said: OtherOrg, key: KeyDep, orgs: OrgsDep, members: MembersDep, keys: KeysDep
) -> dict[str, Any]:
    """A key for the same person, in the org named, in this key's world, with what their role
    opens there. 403 when the org is not one of theirs."""
    person = await _the_person(key, members)
    org = await orgs.find(said.org)
    there = None if org is None else await members.by_email(org.id, person.email)
    if org is None or there is None or there.member.status != "active":
        raise HTTPException(403, NOT_THERE.format(org=said.org))
    issued = await keys.issue(
        org=org.id,
        label=key.label,
        env=key.env,
        scopes=for_a_person(there.member.scopes, key.env),
        subject=there.member.id,
        name=there.member.name,
    )
    return issued.as_json


async def _the_person(key: KeyRecord, members: MembersDep) -> Member:
    """The active member this key was minted for; 403 for a machine key or a member gone."""
    if key.subject is None:
        raise HTTPException(403, ONE_ORG_EACH)
    member = await members.find(key.org, key.subject)
    if member is None or member.status != "active":
        raise HTTPException(403, NOT_A_MEMBER)
    return member


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
async def the_other_world(
    said: OtherWorld, key: KeyDep, keys: KeysDep, members: MembersDep
) -> dict[str, Any]:
    """A key for the same person, with what their role opens there, in the world named."""
    try:
        env = an_env(said.env)
    except DeclarationRefused as refused:
        raise HTTPException(400, str(refused)) from refused
    return await for_the_same_person(key, env, key.label, keys, members)


# The one place a key is minted FROM another key: the console's world toggle above, and the card
# that signs a terminal in (api/pairing.py). The scopes come off the MEMBER and not off the key
# that asked — a person's production key does not hold `app`, and reading its scopes would carry
# that absence into the sandbox, where what they run is their own. The role is the source.
async def for_the_same_person(
    key: KeyRecord, env: Env, label: str | None, keys: KeysDep, members: MembersDep
) -> dict[str, Any]:
    """A key for the person this one names, in the world named, with what their role opens there."""
    if key.subject is None:
        raise HTTPException(403, ONE_WORLD_EACH)
    member = await members.find(key.org, key.subject)
    if member is None or member.status != "active":
        raise HTTPException(403, NOT_A_MEMBER)
    issued = await keys.issue(
        org=key.org,
        label=label,
        env=env,
        scopes=for_a_person(member.scopes, env),
        subject=key.subject,
        name=key.name,
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
    """The member this email and password name, in the org named or in the oldest of theirs, and
    a key minted for them."""
    assert said.email is not None and said.password is not None
    try:
        env = an_env(said.env)
    except DeclarationRefused as refused:
        raise HTTPException(400, str(refused)) from refused
    if not throttle.allowed(f"{client} {said.org or '*'}/{said.email}"):
        raise HTTPException(429, TOO_MANY.format(email=said.email))
    nobody = NOBODY_ANYWHERE if said.org is None else NOBODY.format(org=said.org)
    # The password is the PERSON's, whichever org it was chosen in: a row of theirs still
    # invited in this org — made before they existed, or before this rule — is seated with it.
    known = await members.a_persons_password(said.email)
    if known is None or not passwords.matches(said.password, known):
        raise HTTPException(401, nobody)
    kept = await _the_row_for(said, orgs, members)
    if kept is None:
        raise HTTPException(401, nobody)
    member = kept.member
    if member.status == "disabled":
        raise HTTPException(403, DISABLED.format(email=member.email, org=member.org))
    if member.status == "invited":
        seated = await members.join(member.org, member.id, known)
        if seated is None:
            raise HTTPException(403, NOT_YET.format(email=member.email))
        member = seated
    issued = await keys.issue(
        org=member.org,
        label=said.device or LOGGED_IN,
        env=env,
        scopes=for_a_person(member.scopes, env),
        subject=member.id,
        name=member.name,
    )
    return issued.as_json


async def _the_row_for(said: Login, orgs: OrgsDep, members: MembersDep) -> Kept | None:
    """The person's row in the org named; with none named, their oldest row that is not disabled."""
    assert said.email is not None
    if said.org is not None:
        org = await orgs.find(said.org)
        return None if org is None else await members.by_email(org.id, said.email)
    rows = await members.orgs_of(said.email)
    for row in rows:
        if row.status != "disabled":
            return await members.by_email(row.org, said.email)
    return None if not rows else await members.by_email(rows[0].org, said.email)


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


def the_client(request: Request) -> str:
    """Where the knock came from, for the throttle. Unknown is one name, and it is throttled too."""
    return request.client.host if request.client is not None else "unknown"
