"""Sign-in: a password, a one-use code, a paired terminal, a sign-up, or the org's provider."""

import hashlib
import hmac
import ipaddress
import re
import secrets
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from pinecall.domain.errors import (
    NotAllowed,
    NotSignedIn,
)
from pinecall.domain.names import PRODUCTION
from pinecall.domain.org import Org
from pinecall.domain.person import KEY_SCOPES, Key, Member
from pinecall.postgres.pool import Pool
from pinecall.tenancy.keys import Issued, issue, person_key
from pinecall.tenancy.letters import Link, card_link, forgotten_password_letter
from pinecall.tenancy.mail import Outbox, brand_of
from pinecall.tenancy.orgs import create, find
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
)

type Refusal = Literal["wrong", "expired", "burned"]


# What a login code signs a browser in as: a person, or the server's key that asked for it.
type Holder = Member | Key


WORD_BYTES = 24


# A browser signs in with a code, never a key in the URL: a URL lands in histories and logs.
LOGIN_CODE = ("lc_", 300.0)


# Ten minutes to open a browser and sign in.
PAIRING = ("cli_", 600.0)


# Only the opaque state rides the URL; the org, the nonce and the verifier stay here.
HANDSHAKE = ("st_", 600.0)


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


NO_GRANT = "{issuer} refused the code connections: {said}"


NO_IDENTITY = "{issuer} answered the connections without an id_token"


NO_KEY = "{issuer} signed the id_token with a key its JWKS does not publish"


REFUSED = "the id_token did not check out: {said}"


ANOTHER_SIGN_IN = "the id_token answers a different sign-in: its nonce is not this one's"


NOT_THIS_CLIENTS = "the id_token was issued to {azp!r}: this gateway is {client_id!r}"


NO_EMAIL = "{issuer} vouched for somebody it gave no email address for"


NOT_VERIFIED = "{issuer} has not verified {email}: nobody is seated on an unverified address"


NOBODY_HERE = "nobody in {org} answers to {email}, and it seats nobody it was not told to"


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


# Six tries at six digits is a guess in 166 666; a code burned is sent again.
CODE_DIGITS = 6


CODE_TTL_S = 15 * 60.0


ATTEMPTS = 6


A_BROWSER = "console"


SIGNED_UP = "signup"


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


# Every attempt counts, not only the misses: counting misses would say which guess was right.
TRIES = 5


TOO_MANY = "too many attempts for {email}: try again in a minute"


WINDOW_S = 60.0


# Names are the attacker's to choose, so quiet ones are swept past this many.
SWEEP_AT = 1024


class ProviderRefusal(BaseModel):
    """What an identity provider says when it refuses: its error and the words it gives."""

    model_config = ConfigDict(extra="ignore")

    error: str | None = None
    error_description: str | None = None


@dataclass(frozen=True)
class OneUseValue[T]:
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
        self.minted: dict[str, OneUseValue[T]] = {}

    def word(self) -> str:
        """A new word, not yet holding anything."""
        return f"{self.prefix}{secrets.token_urlsafe(WORD_BYTES)}"

    def keep(self, word: str, value: T) -> float:
        """Hold the value under the word until it dies, and say when."""
        self._forget_the_dead()
        expires_at = self.clock() + self.ttl_s
        self.minted[word] = OneUseValue(value, expires_at)
        return expires_at

    def mint(self, value: T) -> tuple[str, float]:
        """A new word holding the value, and when it dies."""
        word = self.word()
        return word, self.keep(word, value)

    def read(self, word: str) -> OneUseValue[T] | None:
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
        for word in [word for word, minted in self.minted.items() if minted.expires_at <= now]:
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
class WaitingTerminal:
    """A terminal waiting: what it calls itself, and the key a browser gave it once approved."""

    device: str | None = None
    key: str | None = None
    org: str | None = None


@dataclass(frozen=True)
class TerminalApproval:
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
    code_expires_at: float


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


class Pairings:
    """Terminals waiting for a browser to sign them in."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        """No terminal waiting."""
        self.words: OneUse[WaitingTerminal] = OneUse(PAIRING, clock)

    def open(self, device: str | None = None) -> tuple[str, float]:
        """The word a terminal prints, and when it dies."""
        return self.words.mint(WaitingTerminal(device))

    def asking(self, code: str) -> TerminalApproval | None:
        """What the approving card shows, without spending the word; None for a dead one."""
        minted = self.words.read(code)
        if minted is None:
            return None
        return TerminalApproval(
            minted.value.device, minted.expires_at, answered=minted.value.key is not None
        )

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
    def begin(self, signup: Signup) -> tuple[str, float]:
        """Keep the sign-up and hand back the code to mail, and when it dies."""
        self._forget_the_dead()
        code = f"{secrets.randbelow(10**CODE_DIGITS):0{CODE_DIGITS}d}"
        salt = secrets.token_bytes(16)
        expires_at = self.clock() + CODE_TTL_S
        self.pending[signup.email] = Pending(signup, _hashed(salt, code), salt, expires_at)
        return code, expires_at

    def renewed(self, email: str) -> tuple[Signup, str] | None:
        """A new code for a sign-up still waiting, with its time again; None for nobody's."""
        self._forget_the_dead()
        found = self.pending.get(email)
        return None if found is None else (found.signup, self.begin(found.signup)[0])

    def verify(self, email: str, code: str) -> Signup | Refusal:
        """The sign-up, taken out, when the code is its own; else why not."""
        found = self.pending.get(email)
        if found is None:
            return "wrong"
        if found.expires_at <= self.clock():
            del self.pending[email]
            return "expired"
        if found.attempts >= ATTEMPTS:
            return "burned"
        if not hmac.compare_digest(found.code_hash, _hashed(found.salt, code)):
            self.pending[email] = replace(found, attempts=found.attempts + 1)
            return "burned" if found.attempts + 1 >= ATTEMPTS else "wrong"
        del self.pending[email]
        return found.signup

    def _forget_the_dead(self) -> None:
        now = self.clock()
        for email in [
            email for email, pending in self.pending.items() if pending.expires_at <= now
        ]:
            del self.pending[email]


class Throttle:
    """How many times a name knocked in the last minute: five, and the sixth waits."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        """A throttle nobody knocked at."""
        self.clock = clock
        self.knocks: dict[str, deque[float]] = {}

    def allowed(self, name: str) -> bool:
        """Count a knock, and say whether it is within the window's tries."""
        now = self.clock()
        if len(self.knocks) >= SWEEP_AT:
            for quiet in [
                address for address, at in self.knocks.items() if at[-1] <= now - WINDOW_S
            ]:
                del self.knocks[quiet]
        knocks = self.knocks.setdefault(name, deque())
        while knocks and knocks[0] <= now - WINDOW_S:
            knocks.popleft()
        if len(knocks) >= TRIES:
            return False
        knocks.append(now)
        return True


# In memory: each dies with the process, and a restart only makes a person ask again.
@dataclass(frozen=True)
class SignIns:
    """What sign-in keeps between two requests: codes, terminals, sign-ups, handshakes, knocks."""

    codes: OneUse[Holder]
    pairings: Pairings
    signups: Signups
    handshakes: OneUse[Handshake]
    throttle: Throttle

    @classmethod
    def fresh(cls, clock: Callable[[], float] = time.time) -> "SignIns":
        """Nothing kept yet, every word and knock timed by the clock."""
        return cls(
            codes=OneUse(LOGIN_CODE, clock),
            pairings=Pairings(clock),
            signups=Signups(clock),
            handshakes=OneUse(HANDSHAKE, clock),
            throttle=Throttle(clock),
        )


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
    key, secret = await person_key(pool, member, label=asking.device)
    return SignedIn(key, secret, member)


async def orgs_signed_into(pool: Pool, email: str, password: str) -> list[Member]:
    """The orgs an address and password open, minting nothing; none for a wrong password."""
    if not await matches(password, await password_of(pool, email)):
        return []
    return [item for item in await orgs_of(pool, email) if item.status != "disabled"]


# A key's code gives a copy of it, the person it names included, dying when it dies.
async def sign_in_with_code(
    pool: Pool, codes: OneUse[Holder], code: str, *, device: str | None = None
) -> SignedIn | None:
    """The key the code was minted for, spent; None for a code dead, spent or nobody's."""
    holder = codes.spend(code)
    if holder is None:
        return None
    label = device or A_BROWSER
    if isinstance(holder, Member):
        key, secret = await person_key(pool, holder, label=label)
        return SignedIn(key, secret, holder)
    copied = Issued(
        org=holder.org,
        env=holder.env,
        scopes=holder.scopes,
        label=label,
        subject=holder.subject,
        name=holder.name,
        created_by=holder.subject,
        expires_at=holder.expires_at,
    )
    key, secret = await issue(pool, copied)
    return SignedIn(key, secret)


# The scopes are the member's role today, not the asking key's.
async def another_key(
    pool: Pool, key: Key, member: Member | None, *, device: str | None = None
) -> SignedIn:
    """The same person's key for another device, never outliving the key that asked."""
    if key.subject is None or member is None:
        raise NotAllowed(NOT_A_PERSONS)
    if member.status != "active":
        raise NotAllowed(NOT_A_MEMBER)
    minted, secret = await person_key(pool, member, parent=key, label=device)
    return SignedIn(minted, secret, member)


# A person of the box is let into any org, as an admin; anybody else only into an org of theirs.
async def key_in(pool: Pool, member: Member, org: str, *, label: str | None = None) -> SignedIn:
    """The person's key in another org: their membership there, or a visit if they run the box."""
    there = await by_email(pool, org, member.email)
    if there is not None and there.status == "active":
        key, secret = await person_key(pool, there, label=label)
        return SignedIn(key, secret, there)
    if not member.operator or await find(pool, org) is None:
        raise NotAllowed(NOT_THEIRS.format(org=org))
    visit = Issued(
        org=org,
        env=PRODUCTION,
        scopes=KEY_SCOPES,
        label=label or A_BROWSER,
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
        # Asked before minting: a reset spends the older links, so an unmailable one would kill
        # a link an admin handed over.
        if await outbox.mailbox_for(row.org) is None:
            continue
        link = await reset(pool, row.org, row.id)
        if link is None or link.token is None:
            continue
        org = await find(pool, row.org)
        data = Link(
            org=org.name if org else row.org, link=card_link(base, link.token), dies=link.expires_at
        )
        await outbox.post(row.org, forgotten_password_letter(row.email, data, await brand_of(pool)))
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
    key, secret = await person_key(pool, admin, label=signup.device or SIGNED_UP)
    code, code_expires_at = codes.mint(admin)
    return Founded(
        org=org,
        admin=admin,
        signed_in=SignedIn(key, secret, admin),
        code=code,
        code_expires_at=code_expires_at,
    )


# Never the body whole: a token endpoint that echoes the request would show the client secret.
def why_refused(answer: httpx.Response) -> str:
    """The reason a provider gave for refusing, or its HTTP status when it gave none."""
    try:
        payload = ProviderRefusal.model_validate(answer.json())
    except (ValueError, ValidationError):
        return f"HTTP {answer.status_code}"
    return payload.error_description or payload.error or f"HTTP {answer.status_code}"


def an_address(host: str) -> bool:
    """Whether the host is an IP address rather than a name."""
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return True


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


def _hashed(salt: bytes, code: str) -> bytes:
    return hashlib.sha256(salt + code.encode()).digest()
