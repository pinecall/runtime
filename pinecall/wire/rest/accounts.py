"""The account doors' bodies: sign-in, keys, people, sign-up, SSO, mail, who a key is, policy."""

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import Field

from pinecall.domain.names import Env
from pinecall.domain.person import Key, Member, MemberStatus, Role
from pinecall.wire.frames import WireModel

# ── people ──


class MemberRow(WireModel):
    """A member as every members door answers one: never a password nor a link."""

    id: str
    email: str
    name: str
    role: Role
    agents: list[str]
    status: MemberStatus
    scopes: list[str]
    operator: bool
    # The production switch, or being an admin.
    production: bool
    verified: bool

    @classmethod
    def of(cls, member: Member) -> Self:
        """The member as a row."""
        return cls(
            id=member.id,
            email=member.email,
            name=member.name,
            role=member.role,
            agents=sorted(member.agents),
            status=member.status,
            scopes=sorted(member.scopes),
            operator=member.operator,
            production=member.opens_production,
            verified=member.verified,
        )


class MemberList(WireModel):
    """GET /v1/members, the answer: every member of the org, oldest first."""

    members: list[MemberRow]


class InviteMemberRequest(WireModel):
    """POST /v1/members: who, the role, the agents they see (none is every one), production."""

    email: str
    name: str
    role: str
    agents: list[str] = Field(default_factory=list[str])
    production: bool = False


class InvitationResponse(WireModel):
    """An invitation or a reset: the member, the link's token once (null when only mailed)."""

    member: MemberRow
    token: str | None
    expires_at: datetime | None
    mailed: bool


class ChangeMemberRequest(WireModel):
    """PATCH /v1/members/{id}: what is named changes, what is left out stays."""

    role: str | None = None
    agents: list[str] | None = None
    status: str | None = None
    production: bool | None = None


class AcceptInvitationRequest(WireModel):
    """POST /v1/invitations/{token}: the password chosen, and what the first key is called."""

    password: str
    device: str | None = None


# ── keys ──


class KeyIssuedResponse(WireModel):
    """Every door that mints a key: the key this once, and what it was written under."""

    key: str
    key_id: str
    org: str
    label: str | None
    env: Env
    scopes: list[str]
    subject: str | None
    name: str | None

    @classmethod
    def of(cls, key: Key, secret: str) -> Self:
        """The key just minted, and its secret this once."""
        return cls(
            key=secret,
            key_id=key.key_id,
            org=key.org,
            label=key.label,
            env=key.env,
            scopes=sorted(key.scopes),
            subject=key.subject,
            name=key.name,
        )


class FirstKeyResponse(WireModel):
    """POST /v1/invitations/{token}: the member's first key, and the member now active."""

    key: str
    key_id: str
    org: str
    label: str | None
    env: Env
    scopes: list[str]
    subject: str | None
    name: str | None
    member: MemberRow


class OrgMadeResponse(WireModel):
    """POST /v1/signup/verify: the admin's key, the org's slug, and the code for the console."""

    key: str
    key_id: str
    org: str
    label: str | None
    env: Env
    scopes: list[str]
    subject: str | None
    name: str | None
    slug: str
    member: MemberRow
    code: str
    code_expires_at: float


class CreateKeyRequest(WireModel):
    """POST /v1/keys: a server's token, named, for one world."""

    label: str
    env: str


class KeyRow(WireModel):
    """GET /v1/keys, one row: a key by its fingerprint, never the key."""

    fingerprint: str
    label: str | None
    kind: Literal["person", "server"]
    # Only a server's token is bound to one world.
    env: Env | None
    name: str | None
    created_by: str | None
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None
    scopes: list[str]


class RevokeKeyResponse(WireModel):
    """POST /v1/keys/{fingerprint}/revoke, the answer."""

    fingerprint: str
    revoked: bool


# ── who a key is ──


class WhoamiResponse(WireModel):
    """GET /v1/whoami: whose key this is and what it opens; never the key nor its hash."""

    org: str
    # None only when the org's row is gone.
    slug: str | None
    key_id: str
    label: str | None
    # The world this request acts in.
    env: Env
    scopes: list[str]
    subject: str | None
    name: str | None
    # The one name a person carries into every org; None for a server's token.
    email: str | None
    operator: bool
    # A person of the box inside an org they are no member of.
    visiting: bool
    production: bool


class BoxIdentityResponse(WireModel):
    """GET /v1/ops/whoami: the box an operator's key opens, and who holds the key."""

    operator: bool
    version: str
    domain: str | None
    # None for the box's own key.
    name: str | None
    org: str | None


class BrandRow(WireModel):
    """What the box's pages and letters are called and painted with."""

    name: str
    logo_url: str | None
    accent: str


class GatewayInfoResponse(WireModel):
    """GET /.well-known/pinecall: what a sign-in page reads before anybody holds a key."""

    version: str
    signup: bool
    # 0 is no rule.
    min_password: int
    # Whether the box itself can post a letter; an org's own mailbox is not counted.
    mail: bool
    brand: BrandRow
    google: bool
    # The world this name is, and the other world's address; None on a box that named no world.
    world: Env | None = None
    elsewhere: str | None = None


# ── signing in ──


class SignInRequest(WireModel):
    """POST /v1/login: an address and a password, the org when there are several, or a code."""

    org: str | None = None
    email: str | None = None
    password: str | None = None
    code: str | None = None
    # What the minted key is called in the org's list, so it is revoked on its own.
    device: str | None = None


class SignInOrgsRequest(WireModel):
    """POST /v1/login/orgs: the address and the password, to list what they open."""

    email: str
    password: str


class SignInOrgRow(WireModel):
    """One org a password opens, and the person's role there."""

    org: str
    slug: str
    name: str
    role: Role


class SignInOrgsResponse(WireModel):
    """POST /v1/login/orgs, the answer: the orgs, oldest first."""

    orgs: list[SignInOrgRow]


class OrgOfPersonRow(WireModel):
    """GET /v1/login/orgs, one org: the role there, and whether this key is the one opening it."""

    org: str
    slug: str | None
    name: str | None
    # A role, or "operator" for an org of the box a person runs and is no member of.
    role: str
    status: MemberStatus
    here: bool
    member: bool


class OrgsOfPersonResponse(WireModel):
    """GET /v1/login/orgs, the answer: every org the key's person may open, oldest first."""

    orgs: list[OrgOfPersonRow]


class SwitchOrgRequest(WireModel):
    """POST /v1/login/org: the org to be signed into, by id or slug."""

    org: str


class MintCodeResponse(WireModel):
    """A one-use word and when it dies, in epoch seconds: a login code, a terminal's pairing."""

    code: str
    expires_at: float


class ForgotPasswordRequest(WireModel):
    """POST /v1/login/reset: the address whose password was forgotten."""

    email: str


class EmptyResponse(WireModel):
    """An answer whose status says everything: `{}`."""


class OpenPairingRequest(WireModel):
    """POST /v1/login/pairings: what the terminal calls itself."""

    device: str | None = None


class PairingStatusResponse(WireModel):
    """GET /v1/login/pairings/{code}: which terminal a browser is about to sign in."""

    device: str | None
    expires_at: float
    answered: bool


class PairingApprovedResponse(WireModel):
    """POST /v1/login/pairings/{code}: the terminal signed in, and into which org."""

    device: str | None
    org: str


class KeyCollectedResponse(WireModel):
    """GET /v1/login/pairings/{code}/key: the terminal's key, once."""

    key: str


# ── sign-up ──


class SignupRequest(WireModel):
    """POST /v1/signup: the org's slug and name, the person, and their password."""

    org: str
    name: str | None = None
    email: str
    person: str
    password: str
    device: str | None = None


class CodeMailedResponse(WireModel):
    """POST /v1/signup, the answer: where the code went and when it dies; never the code."""

    email: str
    code_expires_at: float


class VerifySignupRequest(WireModel):
    """POST /v1/signup/verify: the address and the six digits mailed to it."""

    email: str
    code: str
    device: str | None = None


class ResendCodeRequest(WireModel):
    """POST /v1/signup/resend: the address a sign-up is waiting on."""

    email: str


# ── the org's identity provider ──


class OrgSsoRequest(WireModel):
    """PUT /v1/org/sso: the provider whole, its client secret included every time."""

    issuer: str
    client_id: str
    client_secret: str
    domains: list[str]
    # Who the provider seats unasked; None seats nobody it was not told to.
    role: str | None = None
    required: bool = False


class OrgSsoResponse(WireModel):
    """GET /v1/org/sso: the org's provider, never its secret, and the URI to register there."""

    configured: bool
    issuer: str | None
    client_id: str | None
    domains: list[str]
    role: Role | None
    required: bool
    redirect_uri: str


class DiscoverSsoRequest(WireModel):
    """POST /v1/login/sso/discover: the address whose domain is asked about."""

    email: str


class SsoOrgRow(WireModel):
    """An org that signs in an address's domain with its provider."""

    org: str
    slug: str
    name: str


class DiscoverSsoResponse(WireModel):
    """POST /v1/login/sso/discover, the answer: the orgs, oldest first."""

    orgs: list[SsoOrgRow]


# ── the org's mailbox ──


class OrgMailRequest(WireModel):
    """PUT /v1/org/mail: the org's SMTP account, its password included every time."""

    host: str
    port: int
    security: str = "starttls"
    # Empty for a relay that asks for nothing.
    username: str = ""
    password: str = ""
    sender: str = Field(alias="from")


class OrgMailResponse(WireModel):
    """GET /v1/org/mail: the org's mailbox, never its password, and how its last letter went."""

    configured: bool
    host: str | None
    port: int | None
    security: str | None
    username: str | None
    sender: str | None = Field(alias="from")
    verified_at: datetime | None
    last_error: str | None


class SendTestLetterRequest(WireModel):
    """POST /v1/org/mail/test: who the test letter goes to."""

    to: str


class SendTestLetterResponse(WireModel):
    """POST /v1/org/mail/test, the answer: whether it went, and what the server said if not."""

    sent: bool
    error: str | None


class OrgPolicy(WireModel):
    """GET and PUT /v1/org/policy: the org's compliance settings, replaced whole."""

    retention_days: Annotated[int, Field(gt=0)] | None = None


class OrgPolicyRow(WireModel):
    """GET /v1/org/policy: the settings, who set them last, and when."""

    policy: OrgPolicy
    set_by: str | None
    set_at: float | None
