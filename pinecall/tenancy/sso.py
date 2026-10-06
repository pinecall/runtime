"""Sign-in with the org's identity provider over OpenID Connect: handshake, code, claims."""

import asyncio
import base64
import hashlib
import secrets
from dataclasses import dataclass

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
from pinecall.domain.names import PRODUCTION
from pinecall.domain.org import Org
from pinecall.domain.person import ROLES, Member, Role
from pinecall.postgres.pool import Pool, box_wide
from pinecall.process import resolver
from pinecall.tenancy.admission import admit_seat, quotas_of
from pinecall.tenancy.people import (
    Invitee,
    by_email,
    invite,
    seated,
    vouched,
)
from pinecall.tenancy.signin import (
    A_DOMAIN,
    ALGORITHMS,
    AN_ISSUER,
    ANOTHER_SIGN_IN,
    CONFIGURATION,
    DISABLED,
    DROP_SSO,
    LOCAL_SUFFIXES,
    MISSING,
    NO_EMAIL,
    NO_GRANT,
    NO_IDENTITY,
    NO_KEY,
    NOBODY_HERE,
    NONCE_BYTES,
    NOT_AN_ISSUER,
    NOT_ITS_OWN_ISSUER,
    NOT_REACHED,
    NOT_THIS_CLIENTS,
    NOT_VERIFIED,
    PUT_SSO,
    REFUSED,
    REQUIRED,
    SCOPE,
    SSO,
    SSO_WITH_DOMAIN,
    TIMEOUT_S,
    VERIFIER_BYTES,
    Handshake,
    OneUse,
    an_address,
    why_refused,
)
from pinecall.tenancy.vault import opened, sealed

# The provider is told to send the browser back here, and the org registers it there by hand.
CALLBACK = "/v1/login/sso/callback"


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


async def sso_of(pool: Pool, vault: MultiFernet, org: str) -> OrgSso | None:
    """The org's own provider, its secret opened."""
    async with pool.connection() as connection:
        row = await (await connection.execute(SSO, {"org": org})).fetchone()
    return None if row is None else _sso(vault, row)


# The one read across orgs: the caller shows org names only.
async def sso_with_domain(pool: Pool, vault: MultiFernet, domain: str) -> list[OrgSso]:
    """Every org that signs in the domain's addresses with its own provider, oldest first."""
    with box_wide():
        async with pool.connection() as connection:
            found = await connection.execute(SSO_WITH_DOMAIN, {"domain": domain})
            rows = await found.fetchall()
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


async def handshake(
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
    await states.keep(begun.state, begun)
    return begun


def reachable(url: str, *, issuer: str, field: str) -> str:
    """The URL, when it is https at a public name; refused otherwise, naming the field."""
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL:
        raise NotAllowed(NOT_REACHED.format(issuer=issuer, field=field, url=url)) from None
    host = parsed.host.lower().rstrip(".")
    private = host in {"localhost", ""} or host.endswith(LOCAL_SUFFIXES) or an_address(host)
    if parsed.scheme != "https" or private:
        raise NotAllowed(NOT_REACHED.format(issuer=issuer, field=field, url=url))
    return url


async def discovered(http: httpx.AsyncClient, issuer: str) -> Provider:
    """The provider's endpoints, from its own discovery document."""
    url = f"{await _resolves_public(issuer, issuer=issuer, field='issuer')}{CONFIGURATION}"
    try:
        answer = await http.get(url, timeout=TIMEOUT_S)
        answer.raise_for_status()
        payload = _Discovery.model_validate(answer.json())
    except (httpx.HTTPError, ValueError, ValidationError):
        raise UpstreamFailed(NOT_AN_ISSUER.format(issuer=issuer, path=CONFIGURATION)) from None
    if payload.issuer != issuer:
        raise UpstreamFailed(NOT_ITS_OWN_ISSUER.format(issuer=issuer, said=payload.issuer))
    methods = payload.token_endpoint_auth_methods_supported
    return Provider(
        issuer=issuer,
        authorization_endpoint=_endpoint(
            payload.authorization_endpoint, issuer, "authorization_endpoint"
        ),
        token_endpoint=_endpoint(payload.token_endpoint, issuer, "token_endpoint"),
        jwks_uri=_endpoint(payload.jwks_uri, issuer, "jwks_uri"),
        basic_auth="client_secret_basic" in methods and "client_secret_post" not in methods,
    )


def authorization_url(provider: Provider, client: Client, begun: Handshake) -> str:
    """Where the browser goes to sign in at the provider, with the state and the challenge."""
    challenge = base64.urlsafe_b64encode(hashlib.sha256(begun.verifier.encode()).digest())
    params = httpx.QueryParams(
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
    return str(httpx.URL(provider.authorization_endpoint).copy_merge_params(params))


async def vouched_for(
    http: httpx.AsyncClient, client: Client, begun: Handshake, code: str
) -> Claims:
    """The person the provider signed in, their address verified, from the code it returned."""
    provider = await discovered(http, client.issuer)
    id_token = await _exchanged(http, provider, client, begun, code)
    data = await _claims(http, provider, id_token, client.client_id, begun.nonce)
    if not data.email:
        raise NotSignedIn(NO_EMAIL.format(issuer=client.issuer))
    # An unverified address would let anybody claim a colleague's.
    if not data.email_verified:
        raise NotSignedIn(NOT_VERIFIED.format(issuer=client.issuer, email=data.email))
    return data


async def seat_vouched(pool: Pool, org: Org, sso: OrgSso, claims: Claims) -> Member:
    """The org's member for the address its provider vouched for, seated, invited or made."""
    email = claims.email.strip().lower()
    kept = await by_email(pool, org.id, email)
    if kept is not None and kept.status == "disabled":
        raise NotAllowed(DISABLED.format(email=email, org=org.slug))
    if kept is None:
        if sso.role is None:
            raise NotAllowed(NOBODY_HERE.format(org=org.slug, email=email))
        # A person the provider seats unasked takes a seat like an invited one.
        seats = (await quotas_of(pool, org.id, PRODUCTION)).seats
        await admit_seat(pool, org.id, PRODUCTION, seated=await seated(pool, org.id))
        name = claims.name or email.partition("@")[0]
        kept = (await invite(pool, org.id, Invitee(email, name, sso.role), seats=seats)).member
    seated_now = await vouched(pool, org.id, kept.id)
    if seated_now is None:
        raise NotAllowed(NOBODY_HERE.format(org=org.slug, email=email))
    return seated_now


# Asked before each request: a public name that resolves inside (10.x, a cluster's service) is
# refused like the address itself, and so is a name that resolves to nothing, which a hostile
# resolver could answer otherwise a moment later.
async def _resolves_public(url: str, *, issuer: str, field: str) -> str:
    """The URL, when every address its name resolves to is a public one; refused otherwise."""
    host = httpx.URL(reachable(url, issuer=issuer, field=field)).host
    try:
        addresses = await asyncio.to_thread(resolver.addresses_of, host)
    except OSError:
        addresses = []
    if not resolver.all_public(addresses):
        raise NotAllowed(NOT_REACHED.format(issuer=issuer, field=field, url=url))
    return url


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
    await _resolves_public(provider.token_endpoint, issuer=provider.issuer, field="token_endpoint")
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
        raise NotSignedIn(NO_GRANT.format(issuer=provider.issuer, said=why_refused(answer)))
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
        data = _IdToken.model_validate(decoded)
    except (jwt.InvalidTokenError, ValidationError) as refused:
        raise NotSignedIn(REFUSED.format(said=refused)) from None
    # The nonce binds the token to this sign-in; without it, a token replayed would pass.
    if not secrets.compare_digest(data.nonce, nonce):
        raise NotSignedIn(ANOTHER_SIGN_IN)
    # OIDC Core 3.1.3.7: with several audiences `azp` is required, and it names this client.
    if isinstance(data.aud, list) and len(data.aud) > 1 and data.azp is None:
        raise NotSignedIn(NOT_THIS_CLIENTS.format(azp=None, client_id=client_id))
    if data.azp is not None and data.azp != client_id:
        raise NotSignedIn(NOT_THIS_CLIENTS.format(azp=data.azp, client_id=client_id))
    verified = data.email_verified is True or str(data.email_verified).lower() == "true"
    return Claims(
        subject=data.sub,
        email=data.email,
        email_verified=verified,
        name=data.name or data.given_name,
    )


async def _signing_key(http: httpx.AsyncClient, provider: Provider, id_token: str) -> jwt.PyJWK:
    await _resolves_public(provider.jwks_uri, issuer=provider.issuer, field="jwks_uri")
    try:
        answer = await http.get(provider.jwks_uri, timeout=TIMEOUT_S)
        answer.raise_for_status()
        published = jwt.PyJWKSet.from_dict(answer.json())
        kid = jwt.get_unverified_header(id_token).get("kid")
    except (httpx.HTTPError, ValueError, jwt.PyJWTError):
        raise UpstreamFailed(NO_KEY.format(issuer=provider.issuer)) from None
    # A token that names no key is checked against the one key published, when there is one.
    matching = [key for key in published.keys if kid is None or key.key_id == kid]
    if len(matching) != 1:
        raise UpstreamFailed(NO_KEY.format(issuer=provider.issuer))
    return matching[0]
