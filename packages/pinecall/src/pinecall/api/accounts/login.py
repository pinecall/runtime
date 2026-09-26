"""POST /v1/login: a person's key, minted for them and their device; and the codes that stand in."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from pinecall.accounts import (
    A_BROWSER,
    NOBODY_ANYWHERE,
    NOT_A_MEMBER,
    SigningIn,
    mint_mirrored_key,
    sign_in_with_code,
    sign_in_with_password,
)
from pinecall.api.accounts.api_keys import KeyIssued, wire_key_issued
from pinecall.api.accounts.identity import AtProduction, IdentityDep, require_production
from pinecall.api.accounts.org_sso import SsoDep
from pinecall.api.deps import (
    ExtensionsDep,
    KeyDep,
    KeysDep,
    LoginCodesDep,
    MembersDep,
    OrgsDep,
    SettingsDep,
    ThrottleDep,
)
from pinecall.auth import passwords
from pinecall.auth.env import is_persons_key
from pinecall.auth.identity import Redeemed
from pinecall.types import Role
from pinecall_protocol import WireModel

router = APIRouter()

# The throttle's own sentence. It counts every try, right or wrong: see auth/throttle.py.
TOO_MANY = "too many attempts for {email}: try again in a minute"

# A code is spent on first use and dies in five minutes: the same answer for every way it is gone.
NO_CODE = "no code answers to that: it was used, it expired, or it never existed"

# The redemption's own refusal. A code a server's token or an operator's visit minted names no
# member: there is nobody to seat on the other instance, and a visit is production's alone.
NOT_A_PERSONS_CODE = "this code stands for a server's token or an operator's visit, not a member"

# A login says one of two things, never both and never neither.
ONE_OR_THE_OTHER = "log in with org, email and password, or with a code — one of the two"


class Credentials(WireModel):
    """An email and a password, and nothing else: what the org picker asks with."""

    email: str
    password: str


class Redeeming(WireModel):
    """The one-use code a person carried to the other instance, and nothing else."""

    code: str


class Login(WireModel):
    """A person's email and password, in one of their orgs; or a code somebody's key minted."""

    # Left out, the oldest org the person belongs to: a person with one org never types it.
    org: str | None = None
    email: str | None = None
    password: str | None = None
    code: str | None = None
    # What the key is labelled: this browser, this laptop. Revoked on its own, later.
    device: str | None = None


# The ONE shape a key in the clear travels in, as `Issued.as_json` spells it (auth/keys.py): every
# door that mints a key for a person answers this, and the doors that add to it subclass it.


class WordMinted(WireModel):
    """A one-use word standing for something, and the moment it dies."""

    code: str
    expires_at: float


class OrgToSignInTo(WireModel):
    """One org a password opens, named, and what the person is there."""

    org: str
    slug: str
    name: str
    role: Role


class OrgsToSignInTo(WireModel):
    """POST /v1/login/orgs: the orgs this email and password open, oldest first."""

    orgs: list[OrgToSignInTo]


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
    sso: SsoDep,
    settings: SettingsDep,
    identity: IdentityDep,
    extensions: ExtensionsDep,
) -> KeyIssued:
    """A key for this person and this device, or a refusal that says the least it can."""
    if said.code is not None:
        if said.org is not None or said.email is not None or said.password is not None:
            raise HTTPException(400, ONE_OR_THE_OTHER)
        record = codes.spend(said.code)
        if record is not None:
            return wire_key_issued(
                await sign_in_with_code(record, said.device, keys, settings.world)
            )
        # A code this instance never minted, on a sandbox, was minted at production: the person
        # signed in there and the console carried it across. Production is asked who they are.
        if identity is None:
            raise HTTPException(404, NO_CODE)
        label = said.device or A_BROWSER
        mirrored = await mint_mirrored_key(
            said.code, label, identity, orgs, members, keys, extensions.admitted
        )
        return wire_key_issued(mirrored)
    # A password is production's to check: a sandbox keeps none, and takes a code or nothing.
    require_production(settings)
    if said.email is None or said.password is None:
        raise HTTPException(400, ONE_OR_THE_OTHER)
    if not throttle.allowed(f"{throttle_client(request)} {said.org or '*'}/{said.email}"):
        raise HTTPException(429, TOO_MANY.format(email=said.email))
    signing_in = SigningIn(said.email, said.password, said.org, said.device)
    issued = await sign_in_with_password(signing_in, orgs, members, keys, sso, settings.world)
    return wire_key_issued(issued)


# Before a person picks an org at the console's sign-in: which orgs this email and password open,
# minting nothing. The same throttle and the same one sentence as the login itself, so a wrong
# password says nothing about whether the email exists anywhere.
@router.post("/v1/login/orgs", dependencies=[AtProduction])
async def orgs_to_sign_in_to(
    said: Credentials,
    request: Request,
    orgs: OrgsDep,
    members: MembersDep,
    throttle: ThrottleDep,
) -> OrgsToSignInTo:
    """The orgs this person may sign in to, oldest first; 401 for a wrong email or password."""
    if not throttle.allowed(f"{throttle_client(request)} */{said.email}"):
        raise HTTPException(429, TOO_MANY.format(email=said.email))
    # `matches` takes as long for an address nobody has as for a wrong password (auth/passwords.py):
    # the one sentence would say nothing, and the clock must not say it instead.
    if not await passwords.matches(said.password, await members.a_persons_password(said.email)):
        raise HTTPException(401, NOBODY_ANYWHERE)
    listed: list[OrgToSignInTo] = []
    for row in await members.orgs_of(said.email):
        org = None if row.status == "disabled" else await orgs.find(row.org)
        if org is not None:
            listed.append(OrgToSignInTo(org=org.id, slug=org.slug, name=org.name, role=row.role))
    return OrgsToSignInTo(orgs=listed)


# A key holder — `pinecall start`, a person already in — mints a word a browser can carry in a URL
# instead of the key: `https://<gateway>/a/<agent>?login=<code>`. The word stands for the minting
# key's record and is spent for a NEW key with the same org, world, scopes and person, so the
# browser's key is its own and is revoked on its own.
@router.post("/v1/login/codes")
async def mint_login_code(key: KeyDep, codes: LoginCodesDep) -> WordMinted:
    """A one-use code standing for this key's record, good for five minutes."""
    minted = codes.mint(key)
    return WordMinted(code=minted.code, expires_at=minted.expires_at)


# Production is who says a person is a member. A person signed in here carries a code to the
# sandbox, and the sandbox spends it HERE and mirrors who this answers (api/accounts/identity.py).
# No bearer: the code is the credential, as at POST /v1/login. The member is read again, because a
# code holds a snapshot of the key that minted it and the row may have changed since — and answered
# as it stands, `disabled` included, because a disabled person is exactly what the sandbox must
# learn. The answer is their row: the role, never the snapshot's scopes, never the production
# switch. No throttle: the login's exists because a password can be guessed, and a code is 24 random
# bytes spent once; keyed by client it would count the whole sandbox gateway as one knocker.
@router.post("/v1/login/redeem", dependencies=[AtProduction])
async def redeem(
    said: Redeeming, orgs: OrgsDep, members: MembersDep, codes: LoginCodesDep
) -> Redeemed:
    """Who the code's person is, as production's rows say now; the code is spent either way."""
    record = codes.spend(said.code)
    if record is None:
        raise HTTPException(404, NO_CODE)
    if not is_persons_key(record) or record.subject is None:
        raise HTTPException(403, NOT_A_PERSONS_CODE)
    member = await members.find(record.org, record.subject)
    org = await orgs.find(record.org)
    if member is None or org is None:
        raise HTTPException(403, NOT_A_MEMBER)
    return Redeemed.of(org, member)


def throttle_client(request: Request) -> str:
    """Where the knock came from, for the throttle. Unknown is one name, and it is throttled too."""
    return request.client.host if request.client is not None else "unknown"
