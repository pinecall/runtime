"""OpenID Connect, as much of it as a sign-in needs: discovery, the code exchange, the claims."""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import secrets
from dataclasses import dataclass
from typing import Any

import httpx
import jwt

from pinecall.errors import PinecallError

# What hangs off an issuer. It is a well-known path and not a guess: the spec names it, and an
# issuer that does not answer it is not an OpenID provider, whatever else it is.
CONFIGURATION = "/.well-known/openid-configuration"

# What this gateway asks the IdP for. `openid` is the protocol itself; the other two are what a
# member row needs — the address a person is known by here, and what to call them.
SCOPE = "openid email profile"

# The signature algorithms an id_token may carry. Asymmetric ones only: the whole point of the
# JWKS is that this box verifies with a public key it fetched and holds no signing secret, and an
# allowlist is what stops a token that declares `none` or an HMAC over something we do hold.
ALGORITHMS = ["RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "PS256", "PS384", "PS512"]

# The claims an id_token must carry for this gateway to seat anybody on it. `nonce` is checked
# against the handshake by hand, below, because PyJWT has no claim for it.
REQUIRED = ["iss", "aud", "exp", "iat", "sub"]

# Nothing here waits on somebody else's server for long: a sign-in is a person watching a
# redirect, and an IdP that has not answered in five seconds is an IdP that is down.
TIMEOUT_S = 5.0

# PKCE. The verifier never leaves this box until the exchange, and what travels through the
# browser is its sha256 — so a code stolen from a redirect cannot be spent by whoever took it.
VERIFIER_BYTES = 32

NOT_AN_ISSUER = "{issuer} does not answer {path}: it is not an OpenID provider, or it is down"
NOT_REACHED = (
    "{issuer}'s {field} is {url!r}: an endpoint is https, at a public name, and never an address"
)
NOT_ITS_OWN_TOKEN = "the id_token was issued to {azp!r}: this gateway is {client_id!r}"
NOT_ITS_OWN_ISSUER = "{issuer} publishes a configuration naming {said}: one of the two is wrong"
MISSING = "{issuer}'s configuration names no {field}"
NO_TOKEN = "{issuer} refused the code exchange: {said}"
NO_ID_TOKEN = "{issuer} answered the exchange without an id_token"
NO_KEY = "{issuer} signed the id_token with a key its JWKS does not publish"
REFUSED = "the id_token did not check out: {said}"
ANOTHER_CALL = "the id_token answers a different sign-in: its nonce is not this one's"


class OpenIdRefused(PinecallError):
    """The IdP did not answer, or what it answered does not check out. The sentence says which."""


@dataclass(frozen=True)
class Provider:
    """One IdP's configuration, as it publishes it: where to send a person, and where to ask."""

    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    # `client_secret_post` unless the provider says it takes only basic. Both are the spec's; an
    # IdP that lists neither gets post, which is what every one this box has met accepts.
    basic_auth: bool = False


@dataclass(frozen=True)
class Claims:
    """Who the IdP says this is: the subject it knows them by, their address, and their name."""

    subject: str
    email: str
    email_verified: bool
    name: str | None


# What this box will ever GET or POST at an IdP's word. The issuer is a tenant admin's input and
# the endpoints are whatever that issuer publishes, so without this rule one org could point the
# box at its own metadata server, a database on the box's network, or anything else answering
# http on a private address (2026-09-26). Google's endpoints live on three hosts, so "the
# issuer's own origin" is not the rule; https at a NAME that is not an address is.
def reachable(url: str, *, issuer: str, field: str) -> str:
    """The url when it is one this box may knock at; a refusal naming the field otherwise."""
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL as broken:
        raise OpenIdRefused(NOT_REACHED.format(issuer=issuer, field=field, url=url)) from broken
    host = parsed.host.lower().rstrip(".")
    private = host in ("localhost", "") or host.endswith(_LOCAL_SUFFIXES) or _an_address(host)
    if parsed.scheme != "https" or private:
        raise OpenIdRefused(NOT_REACHED.format(issuer=issuer, field=field, url=url))
    return url


_LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".arpa", ".home", ".lan")


def _an_address(host: str) -> bool:
    """Whether the host is an IP literal, v4 or v6, rather than a name."""
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return True


async def configuration(http: httpx.AsyncClient, issuer: str) -> Provider:
    """The issuer's own configuration document, checked to be the issuer's own."""
    url = f"{reachable(issuer, issuer=issuer, field='issuer')}{CONFIGURATION}"
    try:
        answer = await http.get(url, timeout=TIMEOUT_S)
        answer.raise_for_status()
        said: dict[str, Any] = answer.json()
    except (httpx.HTTPError, ValueError) as unreachable:
        raise OpenIdRefused(
            NOT_AN_ISSUER.format(issuer=issuer, path=CONFIGURATION)
        ) from unreachable
    if str(said.get("issuer", "")) != issuer:
        raise OpenIdRefused(NOT_ITS_OWN_ISSUER.format(issuer=issuer, said=said.get("issuer")))
    methods = [str(one) for one in said.get("token_endpoint_auth_methods_supported") or ()]
    return Provider(
        issuer=issuer,
        authorization_endpoint=_named(said, "authorization_endpoint", issuer),
        token_endpoint=_named(said, "token_endpoint", issuer),
        jwks_uri=_named(said, "jwks_uri", issuer),
        basic_auth="client_secret_basic" in methods and "client_secret_post" not in methods,
    )


def new_pkce_verifier() -> str:
    """One PKCE verifier: 256 bits of CSPRNG, url-safe, kept on this box until the exchange."""
    return secrets.token_urlsafe(VERIFIER_BYTES)


def pkce_challenge(verifier: str) -> str:
    """What travels through the browser: the verifier's sha256, base64url, no padding."""
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def authorization_url(
    provider: Provider,
    client_id: str,
    redirect_uri: str,
    *,
    state: str,
    nonce: str,
    verifier: str,
) -> str:
    """The authorization URL a person is redirected to, with this sign-in's state and nonce."""
    asked = httpx.QueryParams(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": SCOPE,
            "state": state,
            "nonce": nonce,
            "code_challenge": pkce_challenge(verifier),
            "code_challenge_method": "S256",
        }
    )
    return str(httpx.URL(provider.authorization_endpoint).copy_merge_params(asked))


async def exchange(
    http: httpx.AsyncClient,
    provider: Provider,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
    verifier: str,
) -> str:
    """The authorization code spent for an id_token. The secret reaches the IdP and nowhere else."""
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": verifier,
    }
    # Basic when the provider takes only that, and the secret in the form otherwise: both are
    # the spec's, and httpx's own sentinel is what says "whatever the client does by default".
    auth: Any = (client_id, client_secret) if provider.basic_auth else httpx.USE_CLIENT_DEFAULT
    if not provider.basic_auth:
        form["client_secret"] = client_secret
    try:
        answer = await http.post(provider.token_endpoint, data=form, auth=auth, timeout=TIMEOUT_S)
    except httpx.HTTPError as unreachable:
        raise OpenIdRefused(
            NO_TOKEN.format(issuer=provider.issuer, said=str(unreachable))
        ) from unreachable
    if answer.status_code >= httpx.codes.BAD_REQUEST:
        raise OpenIdRefused(NO_TOKEN.format(issuer=provider.issuer, said=_why(answer)))
    try:
        granted: dict[str, Any] = answer.json() or {}
    except ValueError as not_json:
        raise OpenIdRefused(NO_ID_TOKEN.format(issuer=provider.issuer)) from not_json
    id_token = str(granted.get("id_token") or "")
    if not id_token:
        raise OpenIdRefused(NO_ID_TOKEN.format(issuer=provider.issuer))
    return id_token


async def claims(
    http: httpx.AsyncClient, provider: Provider, id_token: str, client_id: str, nonce: str
) -> Claims:
    """The id_token's claims, once its signature, issuer, audience, expiry and nonce check out."""
    key = await _the_signing_key(http, provider, id_token)
    try:
        said: dict[str, Any] = jwt.decode(
            id_token,
            key=key,
            algorithms=ALGORITHMS,
            audience=client_id,
            issuer=provider.issuer,
            options={"require": REQUIRED},
        )
    except jwt.InvalidTokenError as refused:
        raise OpenIdRefused(REFUSED.format(said=refused)) from refused
    # Not PyJWT's to check, and the one claim that says this token answers THIS sign-in: an
    # id_token replayed from another one is valid in every other way.
    if not secrets.compare_digest(str(said.get("nonce") or ""), nonce):
        raise OpenIdRefused(ANOTHER_CALL)
    # PyJWT accepts the token when this client is ANY of several audiences. OpenID Connect Core
    # 3.1.3.7 then asks two more things: with more than one audience `azp` must be there, and
    # whenever it is there it names this client — a token minted for another client of the same
    # IdP that lists us as a second audience is not ours.
    audience: object = said.get("aud")
    azp = said.get("azp")
    if isinstance(audience, list) and len(audience) > 1 and azp is None:  # pyright: ignore[reportUnknownArgumentType] — a list off a JWT is a list of whatever it says
        raise OpenIdRefused(NOT_ITS_OWN_TOKEN.format(azp=None, client_id=client_id))
    if azp is not None and str(azp) != client_id:
        raise OpenIdRefused(NOT_ITS_OWN_TOKEN.format(azp=str(azp), client_id=client_id))
    verified = said.get("email_verified")
    name = said.get("name") or said.get("given_name")
    return Claims(
        subject=str(said["sub"]),
        email=str(said.get("email") or ""),
        # Absent is NOT verified: an IdP that does not say has not said yes.
        email_verified=verified is True or str(verified).lower() == "true",
        name=None if name is None else str(name),
    )


async def _the_signing_key(http: httpx.AsyncClient, provider: Provider, id_token: str) -> Any:
    """The public key this token names, out of the issuer's JWKS, fetched now."""
    try:
        answer = await http.get(provider.jwks_uri, timeout=TIMEOUT_S)
        answer.raise_for_status()
        published = jwt.PyJWKSet.from_dict(answer.json())
        header: dict[str, Any] = jwt.get_unverified_header(id_token)
        kid = str(header.get("kid") or "")
    except (httpx.HTTPError, ValueError, jwt.PyJWTError) as unreachable:
        raise OpenIdRefused(NO_KEY.format(issuer=provider.issuer)) from unreachable
    try:
        return published[kid].key if kid else _the_only_key(published)
    except KeyError as unpublished:
        raise OpenIdRefused(NO_KEY.format(issuer=provider.issuer)) from unpublished


def _the_only_key(published: jwt.PyJWKSet) -> Any:
    """A token that names no key is verifiable only when the JWKS publishes exactly one."""
    keys: list[jwt.PyJWK] = published.keys
    if len(keys) != 1:
        raise KeyError("no kid, and not one key")
    return keys[0].key


def _named(said: dict[str, Any], field: str, issuer: str) -> str:
    """One endpoint off the configuration, or a refusal naming the field that is not there."""
    found = str(said.get(field) or "")
    if not found:
        raise OpenIdRefused(MISSING.format(issuer=issuer, field=field))
    return reachable(found, issuer=issuer, field=field)


# The IdP's own words, when it has any: `error_description`, then `error`, then the status. Never
# the body whole — a token endpoint that echoes the request would echo the client secret with it.
def _why(answer: httpx.Response) -> str:
    """Why the exchange was refused, in the IdP's vocabulary and nothing of ours."""
    try:
        said: dict[str, Any] = answer.json()
    except ValueError:
        return f"HTTP {answer.status_code}"
    named = said.get("error_description") or said.get("error")
    return f"HTTP {answer.status_code}" if named is None else str(named)
