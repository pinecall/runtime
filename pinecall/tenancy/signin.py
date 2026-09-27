"""Sign-in: a password, a one-use code, a paired terminal, a sign-up, or the org's provider."""

import base64
import hashlib
import hmac
import ipaddress
import re
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Literal

import httpx
import jwt
from cryptography.fernet import MultiFernet
from psycopg.rows import DictRow
from pydantic import BaseModel, ConfigDict, ValidationError

from pinecall.domain.errors import (
    DeclarationRefused,
    NotAllowed,
    NotSignedIn,
    UpstreamFailed,
)
from pinecall.domain.types import KEY_SCOPES, PRODUCTION, ROLES, Key, Member, Org, Role
from pinecall.postgres.pool import Pool
from pinecall.tenancy.admission import admit_seat
from pinecall.tenancy.keys import Issued, issue, person_key
from pinecall.tenancy.mail import Link, Outbox, brand_of, card_link, forgotten_password_letter
from pinecall.tenancy.orgs import create, find, quotas_of
from pinecall.tenancy.people import (
    Invitee,
    accept,
    by_email,
    invite,
    join,
    matches,
    orgs_of,
    password_of,
    reset,
    seated,
    vouched,
)
from pinecall.tenancy.vault import opened, sealed

# ── one-use words ──

WORD_BYTES = 24
# A browser signs in with a code, never a key in the URL: a URL lands in histories and logs.
LOGIN_CODE = ("lc_", 300.0)
# Ten minutes to open a browser and sign in.
PAIRING = ("cli_", 600.0)
# Only the opaque state rides the URL; the org, the nonce and the verifier stay here.
HANDSHAKE = ("st_", 600.0)

# ── refusals ──

# One sentence for a wrong org, address or password: nobody learns who is a member.
NOBODY = "nobody answers to that email and password"
# These are said only once the password matched, so a stranger learns nothing from them.
NOT_YET = "{email} has not accepted their invitation yet: open the link and choose a password"
DISABLED = "{email} is disabled in {org}"
WITH_THE_PROVIDER = "{org} signs in with its identity provider: open /v1/login/sso?org={org}"
NOT_A_MEMBER = "the person this key was minted for is no longer an active member of this org"
NOT_A_PERSONS = "a server's token names nobody: a person's key signs another device in"
NOT_THEIRS = "{org} is not an org of this person's"
NOT_AN_ISSUER = "{issuer} does not answer {path}: it is not an OpenID provider, or it is down"
NOT_REACHED = (
    "{issuer}'s {field} is {url!r}: an endpoint is https, at a public name, never an address"
)
NOT_ITS_OWN_ISSUER = "{issuer} publishes a configuration naming {said}: one of the two is wrong"
MISSING = "{issuer}'s configuration names no {field}"
NO_GRANT = "{issuer} refused the code exchange: {said}"
NO_IDENTITY = "{issuer} answered the exchange without an id_token"
NO_KEY = "{issuer} signed the id_token with a key its JWKS does not publish"
REFUSED = "the id_token did not check out: {said}"
ANOTHER_SIGN_IN = "the id_token answers a different sign-in: its nonce is not this one's"
NOT_THIS_CLIENTS = "the id_token was issued to {azp!r}: this gateway is {client_id!r}"
NO_EMAIL = "{issuer} vouched for somebody it gave no email address for"
NOT_VERIFIED = "{issuer} has not verified {email}: nobody is seated on an unverified address"
NOBODY_HERE = "nobody in {org} answers to {email}, and it seats nobody it was not told to"

# ── OpenID ──

CONFIGURATION = "/.well-known/openid-configuration"
SCOPE = "openid email profile"
# Asymmetric only: `none`, and HMAC forged with a secret this gateway holds, never pass.
ALGORITHMS = ["RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "PS256", "PS384", "PS512"]
REQUIRED = ["iss", "aud", "exp", "iat", "sub"]
TIMEOUT_S = 5.0
# Only the verifier's sha256 crosses the browser, so a stolen code is worth nothing.
VERIFIER_BYTES = 32
NONCE_BYTES = 24
# The issuer and the endpoints are the tenant's to write, so only https at a public name is
# fetched: never an address, never a local name.
LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".arpa", ".home", ".lan")
# No query or fragment: it is compared with every id_token's `iss` as a string.
AN_ISSUER = re.compile(r"^https://[^\s?#]+[^/\s?#]$")
A_DOMAIN = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")

# ── sign-ups ──

# Six tries at six digits is a guess in 166 666; a code burned is sent again.
CODE_DIGITS = 6
CODE_TTL_S = 15 * 60.0
ATTEMPTS = 6

LOGGED_IN = "login"
A_BROWSER = "console"

SSO = """
SELECT org, issuer, client_id, ciphertext, domains, role, required FROM org_sso WHERE org = %(org)s
"""
SSO_WITH_DOMAIN = """
SELECT org, issuer, client_id, ciphertext, domains, role, required FROM org_sso
WHERE %(domain)s = ANY (domains) ORDER BY set_at, org
"""
SSO_ONLY = "SELECT 1 FROM org_sso WHERE org = %(org)s AND required"
# Replaced whole, and no secret's history is kept.
PUT_SSO = """
INSERT INTO org_sso (org, issuer, client_id, ciphertext, domains, role, required)
VALUES (%(org)s, %(issuer)s, %(client_id)s, %(ciphertext)s, %(domains)s, %(role)s, %(required)s)
ON CONFLICT (org) DO UPDATE SET
    issuer = excluded.issuer, client_id = excluded.client_id, ciphertext = excluded.ciphertext,
    domains = excluded.domains, role = excluded.role, required = excluded.required,
    set_at = now()
"""
DROP_SSO = "DELETE FROM org_sso WHERE org = %(org)s RETURNING org"

type Refusal = Literal["wrong", "expired", "burned"]
# What a login code signs a browser in as: a person, or the server's key that asked for it.
type Holder = Member | Key


@dataclass(frozen=True)
class Minted[T]:
    """A value kept under a one-use word, until when."""

    value: T
    expires_at: float


# In memory: a word is minted and spent against the same process within minutes, and a
# restart only makes a person ask again.
class OneUse[T]:
    """Words spent once, each dying on its own."""

    def __init__(self, kind: tuple[str, float], clock: Callable[[], float] = time.time) -> None:
        """No word minted yet; the kind is the words' prefix and how long they live."""
        self.prefix, self.ttl_s = kind
        self.clock = clock
        self.minted: dict[str, Minted[T]] = {}

    def word(self) -> str:
        """A new word, not yet holding anything."""
        return f"{self.prefix}{secrets.token_urlsafe(WORD_BYTES)}"

    def keep(self, word: str, value: T) -> float:
        """Hold the value under the word until it dies, and say when."""
        self._forget_the_dead()
        expires_at = self.clock() + self.ttl_s
        self.minted[word] = Minted(value, expires_at)
        return expires_at

    def mint(self, value: T) -> tuple[str, float]:
        """A new word holding the value, and when it dies."""
        word = self.word()
        return word, self.keep(word, value)

    def read(self, word: str) -> Minted[T] | None:
        """What the word holds, without spending it."""
        self._forget_the_dead()
        return self.minted.get(word)

    def spend(self, word: str) -> T | None:
        """What the word holds, spent: it opens nothing after."""
        self._forget_the_dead()
        minted = self.minted.pop(word, None)
        return None if minted is None else minted.value

    def _forget_the_dead(self) -> None:
        now = self.clock()
        for word in [word for word, one in self.minted.items() if one.expires_at <= now]:
            del self.minted[word]


@dataclass(frozen=True)
class SignedIn:
    """The key a sign-in minted, handed over once, and the person it is for."""

    key: Key
    secret: str
    member: Member | None = None


@dataclass(frozen=True)
class Asking:
    """What a person typed: the address, the password, and the org when they have several."""

    email: str
    password: str
    org: str | None = None
    device: str | None = None


@dataclass
class Opened:
    """A terminal waiting: what it calls itself, and the key a browser gave it once approved."""

    device: str | None = None
    key: str | None = None
    org: str | None = None


@dataclass(frozen=True)
class Asked:
    """What the browser approving a terminal sees: which terminal, until when, and if answered."""

    device: str | None
    expires_at: float
    answered: bool


@dataclass(frozen=True)
class Collected:
    """What a terminal collects: the key once, or that it is still waiting."""

    key: str | None
    # False with no key: dead, collected already, or never opened.
    waiting: bool


@dataclass(frozen=True)
class Signup:
    """A sign-up waiting for its code: who, the org they name, and their password hashed."""

    email: str
    slug: str
    person: str
    hashed: str
    name: str | None = None
    device: str | None = None


@dataclass(frozen=True)
class Pending:
    """A sign-up and its code, kept as a salted hash, with its tries and its end."""

    signup: Signup
    code_hash: bytes
    salt: bytes
    expires_at: float
    attempts: int = 0


@dataclass(frozen=True)
class Founded:
    """An org just made, its admin, their first key, and the code that opens the console."""

    org: Org
    admin: Member
    signed_in: SignedIn
    code: str


@dataclass(frozen=True)
class Client:
    """An OpenID provider and this gateway's client at it."""

    issuer: str
    client_id: str
    client_secret: str

    def __post_init__(self) -> None:
        if not AN_ISSUER.match(self.issuer):
            raise DeclarationRefused(
                f"an issuer is an https URL with no query and no trailing slash, not "
                f"{self.issuer!r}: every id_token's `iss` is compared with it as a string"
            )
        if not self.client_id.strip() or not self.client_secret.strip():
            raise DeclarationRefused("a provider's client carries its id and its secret")


@dataclass(frozen=True)
class OrgSso:
    """An org's own identity provider, the domains it admits, and whom it seats unasked."""

    org: str
    client: Client
    # Checked even when the provider vouches: a tenant of Entra can hold outside guests.
    domains: tuple[str, ...]
    # The role an uninvited person gets; None seats nobody uninvited.
    role: Role | None = None
    # No password opens the org; the operator's break-glass turns it off.
    required: bool = False

    def __post_init__(self) -> None:
        if not self.domains:
            raise DeclarationRefused("an SSO configuration names at least one email domain")
        for domain in self.domains:
            if not A_DOMAIN.match(domain):
                raise DeclarationRefused(f"{domain!r} is not an email domain, folded and bare")

    def admits(self, email: str) -> bool:
        """Whether the address is of one of the org's domains."""
        return email.rpartition("@")[2].strip().lower() in self.domains


@dataclass(frozen=True)
class Handshake:
    """A sign-in at a provider, in flight: its state, nonce, verifier and where it returns."""

    state: str
    org: str | None
    nonce: str
    verifier: str
    # The token endpoint compares it with the authorization request's, character for character.
    redirect_uri: str
    # The terminal a `pinecall login` started from, to hand the key to when it returns.
    pairing: str | None = None


@dataclass(frozen=True)
class Provider:
    """The endpoints a provider's discovery names."""

    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    # client_secret_post unless the provider takes only client_secret_basic.
    basic_auth: bool = False


@dataclass(frozen=True)
class Claims:
    """Who a provider vouched for, from an id_token that checked out."""

    subject: str
    email: str
    email_verified: bool
    name: str | None


class _Discovery(BaseModel):
    model_config = ConfigDict(extra="ignore")

    issuer: str = ""
    authorization_endpoint: str = ""
    token_endpoint: str = ""
    jwks_uri: str = ""
    token_endpoint_auth_methods_supported: list[str] = []


class _Granted(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id_token: str = ""


class _Refusal(BaseModel):
    model_config = ConfigDict(extra="ignore")

    error: str | None = None
    error_description: str | None = None


class _IdToken(BaseModel):
    model_config = ConfigDict(extra="ignore")

    sub: str
    aud: str | list[str]
    azp: str | None = None
    nonce: str = ""
    email: str = ""
    email_verified: bool | str | None = None
    name: str | None = None
    given_name: str | None = None


class Pairings:
    """Terminals waiting for a browser to sign them in."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        """No terminal waiting."""
        self.words: OneUse[Opened] = OneUse(PAIRING, clock)

    def open(self, device: str | None = None) -> tuple[str, float]:
        """The word a terminal prints, and when it dies."""
        return self.words.mint(Opened(device))

    def asking(self, code: str) -> Asked | None:
        """What the approving card shows, without spending the word; None for a dead one."""
        minted = self.words.read(code)
        if minted is None:
            return None
        return Asked(minted.value.device, minted.expires_at, answered=minted.value.key is not None)

    def fill(self, code: str, key: str, org: str) -> bool:
        """Hand the terminal the key a browser approved; False when dead or answered already."""
        minted = self.words.read(code)
        if minted is None or minted.value.key is not None:
            return False
        minted.value.key, minted.value.org = key, org
        return True

    def collect(self, code: str) -> Collected:
        """The key, once, as the terminal asks after it."""
        minted = self.words.read(code)
        if minted is None:
            return Collected(key=None, waiting=False)
        if minted.value.key is None:
            return Collected(key=None, waiting=True)
        self.words.spend(code)
        return Collected(key=minted.value.key, waiting=False)


# In memory, so a sign-up nobody verifies leaves no row; a restart costs a resend.
class Signups:
    """Sign-ups waiting for the six digits that were mailed."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        """No sign-up waiting."""
        self.clock = clock
        self.pending: dict[str, Pending] = {}

    # A second sign-up of the address replaces the first: only the newest code works.
    def begin(self, signup: Signup) -> str:
        """Keep the sign-up and hand back the code to mail."""
        self._forget_the_dead()
        code = f"{secrets.randbelow(10**CODE_DIGITS):0{CODE_DIGITS}d}"
        salt = secrets.token_bytes(16)
        expires_at = self.clock() + CODE_TTL_S
        self.pending[signup.email] = Pending(signup, _hashed(salt, code), salt, expires_at)
        return code

    def renewed(self, email: str) -> tuple[Signup, str] | None:
        """A new code for a sign-up still waiting, with its time again; None for nobody's."""
        self._forget_the_dead()
        asked = self.pending.get(email)
        return None if asked is None else (asked.signup, self.begin(asked.signup))

    def verify(self, email: str, code: str) -> Signup | Refusal:
        """The sign-up, taken out, when the code is its own; else why not."""
        asked = self.pending.get(email)
        if asked is None:
            return "wrong"
        if asked.expires_at <= self.clock():
            del self.pending[email]
            return "expired"
        if asked.attempts >= ATTEMPTS:
            return "burned"
        if not hmac.compare_digest(asked.code_hash, _hashed(asked.salt, code)):
            self.pending[email] = replace(asked, attempts=asked.attempts + 1)
            return "burned" if asked.attempts + 1 >= ATTEMPTS else "wrong"
        del self.pending[email]
        return asked.signup

    def _forget_the_dead(self) -> None:
        now = self.clock()
        for email in [email for email, one in self.pending.items() if one.expires_at <= now]:
            del self.pending[email]


async def sign_in_with_password(pool: Pool, asking: Asking) -> SignedIn:
    """A person's key for the address and password; the org named, or the one a password opens."""
    known = await password_of(pool, asking.email)
    if not await matches(asking.password, known) or known is None:
        raise NotSignedIn(NOBODY)
    member = await _row_for(pool, asking)
    if member is None:
        raise NotSignedIn(NOBODY)
    if await _sso_only(pool, member.org):
        raise NotAllowed(WITH_THE_PROVIDER.format(org=await _slug(pool, member.org)))
    if member.status == "disabled":
        raise NotAllowed(DISABLED.format(email=member.email, org=await _slug(pool, member.org)))
    if member.status == "invited":
        # A password set through a link an admin handed over could be anybody's, so only a
        # proven address joins with the password it has.
        seated_now = await join(pool, member.org, member.id, known) if member.verified else None
        if seated_now is None:
            raise NotAllowed(NOT_YET.format(email=member.email))
        member = seated_now
    key, secret = await person_key(pool, member)
    return SignedIn(key, secret, member)


async def orgs_signed_into(pool: Pool, email: str, password: str) -> list[Member]:
    """The orgs an address and password open, minting nothing; none for a wrong password."""
    if not await matches(password, await password_of(pool, email)):
        return []
    return [one for one in await orgs_of(pool, email) if one.status != "disabled"]


async def sign_in_with_code(pool: Pool, codes: OneUse[Holder], code: str) -> SignedIn | None:
    """The key the code was minted for, spent; None for a code dead, spent or nobody's."""
    holder = codes.spend(code)
    if holder is None:
        return None
    if isinstance(holder, Member):
        key, secret = await person_key(pool, holder)
        return SignedIn(key, secret, holder)
    copied = Issued(
        org=holder.org,
        env=holder.env,
        scopes=holder.scopes,
        label=A_BROWSER,
        expires_at=holder.expires_at,
    )
    key, secret = await issue(pool, copied)
    return SignedIn(key, secret)


# The scopes are the member's role today, not the asking key's.
async def another_key(pool: Pool, key: Key, member: Member | None) -> SignedIn:
    """The same person's key for another device, never outliving the key that asked."""
    if key.subject is None or member is None:
        raise NotAllowed(NOT_A_PERSONS)
    if member.status != "active":
        raise NotAllowed(NOT_A_MEMBER)
    minted, secret = await person_key(pool, member, parent=key)
    return SignedIn(minted, secret, member)


# A person of the box is let into any org, as an admin; anybody else only into an org of theirs.
async def key_in(pool: Pool, member: Member, org: str) -> SignedIn:
    """The person's key in another org: their membership there, or a visit if they run the box."""
    there = await by_email(pool, org, member.email)
    if there is not None and there.status == "active":
        key, secret = await person_key(pool, there)
        return SignedIn(key, secret, there)
    if not member.operator or await find(pool, org) is None:
        raise NotAllowed(NOT_THEIRS.format(org=org))
    visit = Issued(
        org=org,
        env=PRODUCTION,
        scopes=KEY_SCOPES,
        label=A_BROWSER,
        subject=member.id,
        name=member.name,
        created_by=member.id,
    )
    key, secret = await issue(pool, visit)
    return SignedIn(key, secret, member)


# Answers the same for an address known or not, and mails in the background, so neither the
# answer nor its time says who is a member.
async def forgotten(pool: Pool, outbox: Outbox, email: str, base: str) -> None:
    """Mail a one-use link that sets the password, through the first org that can send it."""
    for row in await orgs_of(pool, email):
        if row.status != "active" or await _sso_only(pool, row.org):
            continue
        # Asked before minting: a reset spends the older links, so an unmailable one would kill a
        # link an admin handed over.
        if await outbox.mailbox_for(row.org) is None:
            continue
        link = await reset(pool, row.org, row.id)
        if link is None or link.token is None:
            continue
        org = await find(pool, row.org)
        said = Link(
            org=org.name if org else row.org, link=card_link(base, link.token), dies=link.expires_at
        )
        await outbox.post(row.org, forgotten_password_letter(row.email, said, await brand_of(pool)))
        return


# Only once the address is verified, so a sign-up nobody proves never takes a slug.
async def found(pool: Pool, signup: Signup, codes: OneUse[Holder]) -> Founded:
    """The org of a verified sign-up, its admin seated, their key, and the console's code."""
    already = len(await orgs_of(pool, signup.email))
    org = await create(pool, signup.slug, signup.name or signup.slug, already=already)
    invited = await invite(pool, org.id, Invitee(signup.email, signup.person, "admin"), seats=None)
    # A person with a password here is seated at once and keeps it: one password per person.
    admin = invited.member
    if invited.token is not None:
        admin = await accept(pool, invited.token, signup.hashed) or admin
    key, secret = await person_key(pool, admin)
    code, _ = codes.mint(admin)
    return Founded(org=org, admin=admin, signed_in=SignedIn(key, secret, admin), code=code)


async def sso_of(pool: Pool, vault: MultiFernet, org: str) -> OrgSso | None:
    """The org's own provider, its secret opened."""
    async with pool.connection() as connection:
        row = await (await connection.execute(SSO, {"org": org})).fetchone()
    return None if row is None else _sso(vault, row)


# The one read across orgs: the caller shows org names only.
async def sso_with_domain(pool: Pool, vault: MultiFernet, domain: str) -> list[OrgSso]:
    """Every org that signs in the domain's addresses with its own provider, oldest first."""
    async with pool.connection() as connection:
        rows = await (await connection.execute(SSO_WITH_DOMAIN, {"domain": domain})).fetchall()
    return [found for row in rows if (found := _sso(vault, row)) is not None]


async def put_sso(pool: Pool, vault: MultiFernet, sso: OrgSso) -> None:
    """Keep the org's provider, the secret sealed."""
    values = {
        "org": sso.org,
        "issuer": sso.client.issuer,
        "client_id": sso.client.client_id,
        "ciphertext": sealed(vault, sso.client.client_secret),
        "domains": list(sso.domains),
        "role": sso.role,
        "required": sso.required,
    }
    async with pool.connection() as connection:
        await connection.execute(PUT_SSO, values)


async def drop_sso(pool: Pool, org: str) -> bool:
    """Forget the org's provider; passwords open it again. Whether it had one."""
    async with pool.connection() as connection:
        return await (await connection.execute(DROP_SSO, {"org": org})).fetchone() is not None


def handshake(
    states: OneUse[Handshake],
    redirect_uri: str,
    *,
    org: str | None = None,
    pairing: str | None = None,
) -> Handshake:
    """A sign-in at a provider begun: a fresh state, nonce and PKCE verifier, kept here."""
    begun = Handshake(
        state=states.word(),
        org=org,
        nonce=secrets.token_urlsafe(NONCE_BYTES),
        verifier=secrets.token_urlsafe(VERIFIER_BYTES),
        redirect_uri=redirect_uri,
        pairing=pairing,
    )
    states.keep(begun.state, begun)
    return begun


def reachable(url: str, *, issuer: str, field: str) -> str:
    """The URL, when it is https at a public name; refused otherwise, naming the field."""
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL:
        raise NotAllowed(NOT_REACHED.format(issuer=issuer, field=field, url=url)) from None
    host = parsed.host.lower().rstrip(".")
    private = host in {"localhost", ""} or host.endswith(LOCAL_SUFFIXES) or _an_address(host)
    if parsed.scheme != "https" or private:
        raise NotAllowed(NOT_REACHED.format(issuer=issuer, field=field, url=url))
    return url


async def discovered(http: httpx.AsyncClient, issuer: str) -> Provider:
    """The provider's endpoints, from its own discovery document."""
    url = f"{reachable(issuer, issuer=issuer, field='issuer')}{CONFIGURATION}"
    try:
        answer = await http.get(url, timeout=TIMEOUT_S)
        answer.raise_for_status()
        said = _Discovery.model_validate(answer.json())
    except (httpx.HTTPError, ValueError, ValidationError):
        raise UpstreamFailed(NOT_AN_ISSUER.format(issuer=issuer, path=CONFIGURATION)) from None
    if said.issuer != issuer:
        raise UpstreamFailed(NOT_ITS_OWN_ISSUER.format(issuer=issuer, said=said.issuer))
    methods = said.token_endpoint_auth_methods_supported
    return Provider(
        issuer=issuer,
        authorization_endpoint=_endpoint(
            said.authorization_endpoint, issuer, "authorization_endpoint"
        ),
        token_endpoint=_endpoint(said.token_endpoint, issuer, "token_endpoint"),
        jwks_uri=_endpoint(said.jwks_uri, issuer, "jwks_uri"),
        basic_auth="client_secret_basic" in methods and "client_secret_post" not in methods,
    )


def authorization_url(provider: Provider, client: Client, begun: Handshake) -> str:
    """Where the browser goes to sign in at the provider, with the state and the challenge."""
    challenge = base64.urlsafe_b64encode(hashlib.sha256(begun.verifier.encode()).digest())
    asked = httpx.QueryParams(
        {
            "response_type": "code",
            "client_id": client.client_id,
            "redirect_uri": begun.redirect_uri,
            "scope": SCOPE,
            "state": begun.state,
            "nonce": begun.nonce,
            "code_challenge": challenge.decode().rstrip("="),
            "code_challenge_method": "S256",
        }
    )
    return str(httpx.URL(provider.authorization_endpoint).copy_merge_params(asked))


async def vouched_for(
    http: httpx.AsyncClient, client: Client, begun: Handshake, code: str
) -> Claims:
    """The person the provider signed in, their address verified, from the code it returned."""
    provider = await discovered(http, client.issuer)
    id_token = await _exchanged(http, provider, client, begun, code)
    said = await _claims(http, provider, id_token, client.client_id, begun.nonce)
    if not said.email:
        raise NotSignedIn(NO_EMAIL.format(issuer=client.issuer))
    # An unverified address would let anybody claim a colleague's.
    if not said.email_verified:
        raise NotSignedIn(NOT_VERIFIED.format(issuer=client.issuer, email=said.email))
    return said


async def seat_vouched(pool: Pool, org: Org, sso: OrgSso, said: Claims) -> Member:
    """The org's member for the address its provider vouched for, seated, invited or made."""
    email = said.email.strip().lower()
    kept = await by_email(pool, org.id, email)
    if kept is not None and kept.status == "disabled":
        raise NotAllowed(DISABLED.format(email=email, org=org.slug))
    if kept is None:
        if sso.role is None:
            raise NotAllowed(NOBODY_HERE.format(org=org.slug, email=email))
        # A person the provider seats unasked takes a seat like an invited one.
        seats = (await quotas_of(pool, org.id, PRODUCTION)).seats
        await admit_seat(pool, org.id, PRODUCTION, seated=await seated(pool, org.id))
        name = said.name or email.partition("@")[0]
        kept = (await invite(pool, org.id, Invitee(email, name, sso.role), seats=seats)).member
    seated_now = await vouched(pool, org.id, kept.id)
    if seated_now is None:
        raise NotAllowed(NOBODY_HERE.format(org=org.slug, email=email))
    return seated_now


async def _row_for(pool: Pool, asking: Asking) -> Member | None:
    if asking.org is not None:
        org = await find(pool, asking.org)
        return None if org is None else await by_email(pool, org.id, asking.email)
    rows = await orgs_of(pool, asking.email)
    # An org a password opens first; one that signs in only with its provider is refused after.
    for row in rows:
        if row.status != "disabled" and not await _sso_only(pool, row.org):
            return row
    return next((row for row in rows if row.status != "disabled"), rows[0] if rows else None)


async def _sso_only(pool: Pool, org: str) -> bool:
    async with pool.connection() as connection:
        return await (await connection.execute(SSO_ONLY, {"org": org})).fetchone() is not None


async def _slug(pool: Pool, org: str) -> str:
    found_org = await find(pool, org)
    return org if found_org is None else found_org.slug


def _sso(vault: MultiFernet, row: DictRow) -> OrgSso | None:
    secret = opened(vault, row["ciphertext"])
    if not isinstance(secret, str):
        return None
    role = row["role"]
    return OrgSso(
        org=row["org"],
        client=Client(issuer=row["issuer"], client_id=row["client_id"], client_secret=secret),
        domains=tuple(row["domains"]),
        role=role if role in ROLES else None,
        required=row["required"],
    )


def _endpoint(found_url: str, issuer: str, field: str) -> str:
    if not found_url:
        raise UpstreamFailed(MISSING.format(issuer=issuer, field=field))
    return reachable(found_url, issuer=issuer, field=field)


async def _exchanged(
    http: httpx.AsyncClient, provider: Provider, client: Client, begun: Handshake, code: str
) -> str:
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": begun.redirect_uri,
        "client_id": client.client_id,
        "code_verifier": begun.verifier,
    }
    if not provider.basic_auth:
        form["client_secret"] = client.client_secret
    try:
        answer = await http.post(
            provider.token_endpoint,
            data=form,
            auth=(client.client_id, client.client_secret)
            if provider.basic_auth
            else httpx.USE_CLIENT_DEFAULT,
            timeout=TIMEOUT_S,
        )
    except httpx.HTTPError as unreachable:
        raise UpstreamFailed(NO_GRANT.format(issuer=provider.issuer, said=unreachable)) from None
    if answer.status_code >= httpx.codes.BAD_REQUEST:
        raise NotSignedIn(NO_GRANT.format(issuer=provider.issuer, said=_why(answer)))
    try:
        granted = _Granted.model_validate(answer.json())
    except (ValueError, ValidationError):
        raise UpstreamFailed(NO_IDENTITY.format(issuer=provider.issuer)) from None
    if not granted.id_token:
        raise UpstreamFailed(NO_IDENTITY.format(issuer=provider.issuer))
    return granted.id_token


async def _claims(
    http: httpx.AsyncClient, provider: Provider, id_token: str, client_id: str, nonce: str
) -> Claims:
    key = await _signing_key(http, provider, id_token)
    try:
        decoded = jwt.decode(
            id_token,
            key=key,
            algorithms=ALGORITHMS,
            audience=client_id,
            issuer=provider.issuer,
            options={"require": REQUIRED},
        )
        said = _IdToken.model_validate(decoded)
    except (jwt.InvalidTokenError, ValidationError) as refused:
        raise NotSignedIn(REFUSED.format(said=refused)) from None
    # The nonce binds the token to this sign-in; without it, a token replayed would pass.
    if not secrets.compare_digest(said.nonce, nonce):
        raise NotSignedIn(ANOTHER_SIGN_IN)
    # OIDC Core 3.1.3.7: with several audiences `azp` is required, and it names this client.
    if isinstance(said.aud, list) and len(said.aud) > 1 and said.azp is None:
        raise NotSignedIn(NOT_THIS_CLIENTS.format(azp=None, client_id=client_id))
    if said.azp is not None and said.azp != client_id:
        raise NotSignedIn(NOT_THIS_CLIENTS.format(azp=said.azp, client_id=client_id))
    verified = said.email_verified is True or str(said.email_verified).lower() == "true"
    return Claims(
        subject=said.sub,
        email=said.email,
        email_verified=verified,
        name=said.name or said.given_name,
    )


async def _signing_key(http: httpx.AsyncClient, provider: Provider, id_token: str) -> jwt.PyJWK:
    try:
        answer = await http.get(provider.jwks_uri, timeout=TIMEOUT_S)
        answer.raise_for_status()
        published = jwt.PyJWKSet.from_dict(answer.json())
        kid = jwt.get_unverified_header(id_token).get("kid")
    except (httpx.HTTPError, ValueError, jwt.PyJWTError):
        raise UpstreamFailed(NO_KEY.format(issuer=provider.issuer)) from None
    # A token that names no key is checked against the one key published, when there is one.
    matching = [one for one in published.keys if kid is None or one.key_id == kid]
    if len(matching) != 1:
        raise UpstreamFailed(NO_KEY.format(issuer=provider.issuer))
    return matching[0]


# Never the body whole: a token endpoint that echoes the request would show the client secret.
def _why(answer: httpx.Response) -> str:
    try:
        said = _Refusal.model_validate(answer.json())
    except (ValueError, ValidationError):
        return f"HTTP {answer.status_code}"
    return said.error_description or said.error or f"HTTP {answer.status_code}"


def _an_address(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return True


def _hashed(salt: bytes, code: str) -> bytes:
    return hashlib.sha256(salt + code.encode()).digest()
