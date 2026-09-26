"""POST /v1/login: a person's key, minted for them and their device; and the codes that stand in."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from pinecall.api.accounts.api_keys import KeyIssued, wire_key_issued
from pinecall.api.accounts.identity import (
    AtProduction,
    IdentityDep,
    mint_mirrored_key,
    require_production,
)
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
from pinecall.auth.keys import KeyRecord
from pinecall.auth.members import Kept, Members
from pinecall.auth.person_keys import mint_person_key, until
from pinecall.auth.visitor_keys import visitor_email
from pinecall.orgs.org_sso import Sso
from pinecall.orgs.records import Orgs
from pinecall.types import SANDBOX, Env, Role
from pinecall_protocol import WireModel

router = APIRouter()

# One sentence whether the org, the email or the password was wrong: a door that told them apart
# would tell a stranger which orgs and which people exist.
NOBODY = "no member of {org} answers to that email and password"
# The same sentence when no org was named: a person is their email on this box, and a login
# with no org lands in the oldest org they belong to.
# A login by password is asked for with both, or it is a login by code and never reaches here.
BOTH_ARE_NEEDED = "a login by password names both the email and the password"

NOBODY_ANYWHERE = "nobody answers to that email and password"

# The two standings that are not `active`, each with what to do about it. Said only once the
# password matched: a stranger who guessed an email learns nothing about it.
NOT_YET = "{email} has not accepted their invitation yet: open the link and choose a password"
DISABLED = "{email} is disabled in {org}"

# The throttle's own sentence. It counts every try, right or wrong: see auth/throttle.py.
TOO_MANY = "too many attempts for {email}: try again in a minute"

# The org wired an identity provider and said a password opens it no longer. It is said ONLY
# once the password has matched and the row has been found — a wrong password is the one 401 it
# always was, so this sentence tells a stranger nothing about who is a member of what. Somebody
# who holds the right password already holds the right password; what they learn here is where
# to go instead, which is the whole point of saying it.
WITH_THE_PROVIDER = (
    "{org} signs in with its identity provider: open /v1/login/sso?org={org} instead of a password"
)

# A code is spent on first use and dies in five minutes: the same answer for every way it is gone.
NO_CODE = "no code answers to that: it was used, it expired, or it never existed"

# The redemption's own refusal. A code a server's token or an operator's visit minted names no
# member: there is nobody to seat on the other instance, and a visit is production's alone.
NOT_A_PERSONS_CODE = "this code stands for a server's token or an operator's visit, not a member"

# A login says one of two things, never both and never neither.
ONE_OR_THE_OTHER = "log in with org, email and password, or with a code — one of the two"

# What a key is labelled when the person named no device: the door it came through.
LOGGED_IN = "login"
A_BROWSER = "console"

# A key is minted from another only for the person it names: a server's token names nobody.
NOT_A_PERSONS = "a server's token names nobody: a person's key signs another device in"

# The key names a member the table no longer has an active row for: they were removed, or
# disabled while holding a key. Their key still opens what it did until it is revoked; it does
# not mint another.
NOT_A_MEMBER = "the person this key was minted for is no longer an active member of this org"

# An operator inside an org they are no member of (auth/visitor_keys.py) looks at what that org's
# customers reach, from the console. The sandbox is a PERSON's corner of an org, and they are
# nobody's colleague there: there is no corner of theirs to open, and no terminal to sign in.
VISITS_PRODUCTION = (
    "an operator visits an org in production, from the console: the sandbox and a terminal are "
    "a member's — switch back to an org you belong to"
)


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
            return await _with_a_code(record, said.device, keys, settings.world)
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
    return await _with_a_password(
        said, throttle_client(request), orgs, members, keys, throttle, sso, settings.world
    )


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


# The one place a key is minted FROM another key: the card that signs a terminal in
# (api/accounts/pairing.py). The scopes come off the MEMBER and not off the key that asked — the
# role is the source, and a role changed since the asking key was minted is the role now.
async def mint_key_for_same_person(
    key: KeyRecord, label: str | None, keys: KeysDep, members: MembersDep, world: Env
) -> KeyIssued:
    """A key for the person this one names, with what their role opens."""
    if key.subject is None:
        raise HTTPException(403, NOT_A_PERSONS)
    if visitor_email(key.subject) is not None:
        raise HTTPException(403, VISITS_PRODUCTION)
    member = await members.find(key.org, key.subject)
    if member is None or member.status != "active":
        raise HTTPException(403, NOT_A_MEMBER)
    return wire_key_issued(await mint_person_key(keys, member, label, world, minted_from=key))


async def _with_a_password(
    said: Login,
    client: str,
    orgs: OrgsDep,
    members: MembersDep,
    keys: KeysDep,
    throttle: ThrottleDep,
    sso: Sso | None,
    world: Env,
) -> KeyIssued:
    """The member this email and password name, in the org named or in the oldest of theirs, and
    a key minted for them."""
    if said.email is None or said.password is None:
        raise HTTPException(400, BOTH_ARE_NEEDED)
    if not throttle.allowed(f"{client} {said.org or '*'}/{said.email}"):
        raise HTTPException(429, TOO_MANY.format(email=said.email))
    nobody = NOBODY_ANYWHERE if said.org is None else NOBODY.format(org=said.org)
    # The password is the PERSON's, whichever org it was chosen in: a row of theirs still
    # invited in this org — made before they existed, or before this rule — is seated with it.
    known = await members.a_persons_password(said.email)
    if not await passwords.matches(said.password, known) or known is None:
        raise HTTPException(401, nobody)
    kept = await _the_row_for(said, said.email, orgs, members, sso)
    if kept is None:
        raise HTTPException(401, nobody)
    member = kept.member
    # Said after the password matched, and about the org the row is in: a person with two orgs
    # lands in the one a password still opens (below), and only somebody whose every org signs in
    # with a provider is sent to one.
    if await is_sso_only(sso, member.org):
        org = await orgs.find(member.org)
        raise HTTPException(401, WITH_THE_PROVIDER.format(org=org.slug if org else member.org))
    if member.status == "disabled":
        raise HTTPException(403, DISABLED.format(email=member.email, org=member.org))
    if member.status == "invited":
        # Seated with the password they have only once the address is PROVED theirs (0048): a
        # password chosen through a link an admin handed over could be anybody's, and an
        # invited row seated on it was the row the wrong person walked into.
        seated = (
            await members.join(member.org, member.id, known)
            if await members.verified(member.email)
            else None
        )
        if seated is None:
            raise HTTPException(403, NOT_YET.format(email=member.email))
        member = seated
    return wire_key_issued(await mint_person_key(keys, member, said.device or LOGGED_IN, world))


async def _the_row_for(
    said: Login, email: str, orgs: Orgs, members: Members, sso: Sso | None
) -> Kept | None:
    """The person's row in the org named; with none named, their oldest row that is not disabled."""
    if said.org is not None:
        org = await orgs.find(said.org)
        return None if org is None else await members.by_email(org.id, email)
    rows = await members.orgs_of(email)
    # An org that signs in with its provider is passed over here rather than refused: a person of
    # two orgs, one of them on SSO, types no org and lands in the one their password opens. When
    # every org of theirs is on a provider the loop finds none and the fallback below says so.
    for row in rows:
        if row.status != "disabled" and not await is_sso_only(sso, row.org):
            return await members.by_email(row.org, email)
    for row in rows:
        if row.status != "disabled":
            return await members.by_email(row.org, email)
    return None if not rows else await members.by_email(rows[0].org, email)


# None is a box with no vault key: it can read no client secret, so no org signs in with a
# provider there and every one of them is opened by a password. That is also the way back for a
# box whose vault key was lost, and it is deliberate — see orgs/org_sso.py.
async def is_sso_only(sso: Sso | None, org: str) -> bool:
    """Whether this org has said a password opens it no longer."""
    if sso is None:
        return False
    wired = await sso.of(org)
    return wired is not None and wired.required


async def _with_a_code(
    record: KeyRecord, device: str | None, keys: KeysDep, world: Env
) -> KeyIssued:
    """A key of the browser's own minted from the record a code of ours stood for. A person's
    carries no world, whatever world the request that minted the code named, and lives as long as
    a person's key does here — never longer than the key that minted the code."""
    person = is_persons_key(record)
    issued = await keys.issue(
        org=record.org,
        label=device or A_BROWSER,
        env=SANDBOX if person else record.env,
        scopes=record.scopes,
        subject=record.subject,
        name=record.name,
        expires_at=until(world, record) if person else record.expires_at,
    )
    return wire_key_issued(issued)


def throttle_client(request: Request) -> str:
    """Where the knock came from, for the throttle. Unknown is one name, and it is throttled too."""
    return request.client.host if request.client is not None else "unknown"
