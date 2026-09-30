"""Sign-in with an org's identity provider: the redirect out, the callback in, a domain's orgs."""

from typing import Annotated

import httpx
from fastapi import APIRouter, Query, Request
from fastapi.responses import RedirectResponse

from pinecall.domain.errors import (
    DeclarationRefused,
    NotAllowed,
    NotAvailable,
    NotFound,
    PinecallError,
)
from pinecall.domain.person import Member
from pinecall.gateway._deps import GatewayDep, check_knock, client_of, public_url
from pinecall.gateway._gateway import Gateway
from pinecall.tenancy import orgs, signin, sso
from pinecall.tenancy.signin import A_DOMAIN, Handshake
from pinecall.wire.rest.accounts import DiscoverSsoRequest, DiscoverSsoResponse, SsoOrgRow

router = APIRouter()


# The page a sign-in lands on; the URL carries a one-use login code, never a key.
THE_CONSOLE = "/"


# Where a sign-in begun by `pinecall login` lands, to approve the terminal waiting.
THE_CARD = "/cli"


NO_SSO_HERE = "org {org} signs in with no identity provider"


NO_HANDSHAKE = (
    "no sign-in answers to that: it was finished already, it expired, or it never started"
)


ANOTHER_DOMAIN = "{email} is not in a domain {org} signs in with"


# No address is known yet at the redirect, so it is counted by the client's address.
TOO_MANY_SIGN_INS = "too many sign-ins from here: try again in a minute"


THE_PROVIDER_SAID = "the sign-in was refused at the provider: {error}"


NO_CODE_BACK = "the provider sent no authorization code back"


NO_BOX_WIDE = (
    "box-wide sign-in is not in this version: sign in with a password or the org's provider"
)


FOUND = 302


Pairing = Annotated[str | None, Query(description="the terminal waiting to be signed in")]


# The same 404 for an org unknown and one with no provider, so slugs are not enumerated.
@router.get("/v1/login/sso")
async def start_sso(
    org: Annotated[str, Query(description="which org's provider, by id or slug")],
    request: Request,
    gateway: GatewayDep,
    pairing: Pairing = None,
) -> RedirectResponse:
    """302 to the org's provider, with a state, a nonce and a PKCE challenge."""
    connections = gateway.connections
    owner = await orgs.find(connections.pool, org)
    wired = (
        None if owner is None else await sso.sso_of(connections.pool, connections.vault, owner.id)
    )
    if owner is None or wired is None:
        raise NotFound(NO_SSO_HERE.format(org=org))
    check_knock(gateway, f"{client_of(request)} sso/{owner.slug}", TOO_MANY_SIGN_INS)
    provider = await sso.discovered(connections.http, wired.client.issuer)
    redirect_uri = f"{public_url(request, gateway)}{sso.CALLBACK}"
    begun = await sso.handshake(
        gateway.signins.handshakes, redirect_uri, org=owner.id, pairing=pairing
    )
    return RedirectResponse(sso.authorization_url(provider, wired.client, begun), FOUND)


# A refusal mid-redirect goes back to the sign-in page as `?refused=`: a JSON body there is a
# dead end. A state nobody began is the one refusal answered as JSON.
@router.get("/v1/login/sso/callback")
async def finish_sso(
    state: str, gateway: GatewayDep, code: str | None = None, error: str | None = None
) -> RedirectResponse:
    """The code exchanged, the person seated, and 302 to the console with a login code."""
    begun = await gateway.signins.handshakes.spend(state)
    if begun is None:
        raise DeclarationRefused(NO_HANDSHAKE)
    if error is not None:
        return _refused(THE_PROVIDER_SAID.format(error=error))
    if not code:
        return _refused(NO_CODE_BACK)
    try:
        member = await _seated(gateway, begun, code)
    except PinecallError as refusal:
        return _refused(str(refusal))
    login, _ = await gateway.signins.codes.mint(member)
    return RedirectResponse(_landing(begun.pairing, login), FOUND)


# Says which orgs a domain signs in with, never whether the address is anybody's.
@router.post("/v1/login/sso/discover")
async def discover_sso(
    body: DiscoverSsoRequest, request: Request, gateway: GatewayDep
) -> DiscoverSsoResponse:
    """The orgs whose provider signs in this address's domain, oldest first."""
    connections = gateway.connections
    email = body.email.strip().lower()
    check_knock(gateway, f"{client_of(request)} sso/{email}", signin.TOO_MANY.format(email=email))
    domain = email.rpartition("@")[2]
    if not A_DOMAIN.match(domain):
        return DiscoverSsoResponse(orgs=[])
    rows: list[SsoOrgRow] = []
    for wired in await sso.sso_with_domain(connections.pool, connections.vault, domain):
        org = await orgs.find(connections.pool, wired.org)
        if org is not None:
            rows.append(SsoOrgRow(org=org.id, slug=org.slug, name=org.name))
    return DiscoverSsoResponse(orgs=rows)


@router.get("/v1/login/google")
async def google_sign_in() -> RedirectResponse:
    """Box-wide "Continue with Google", which this version does not have."""
    raise NotAvailable(NO_BOX_WIDE)


@router.get("/v1/login/google/callback")
async def google_callback() -> RedirectResponse:
    """Box-wide Google's callback, which this version does not have."""
    raise NotAvailable(NO_BOX_WIDE)


async def _seated(gateway: Gateway, begun: Handshake, code: str) -> Member:
    connections = gateway.connections
    named = begun.org or ""
    wired = await sso.sso_of(connections.pool, connections.vault, named)
    org = await orgs.find(connections.pool, named)
    if wired is None or org is None:
        raise NotFound(NO_SSO_HERE.format(org=named))
    claims = await sso.vouched_for(connections.http, wired.client, begun, code)
    if not wired.admits(claims.email):
        raise NotAllowed(ANOTHER_DOMAIN.format(email=claims.email, org=org.slug))
    return await sso.seat_vouched(connections.pool, org, wired, claims)


def _landing(pairing: str | None, code: str) -> str:
    if pairing is None:
        return f"{THE_CONSOLE}?{httpx.QueryParams({'login': code})}"
    return f"{THE_CARD}?{httpx.QueryParams({'c': pairing, 'login': code})}"


def _refused(sentence: str) -> RedirectResponse:
    return RedirectResponse(f"{THE_CONSOLE}?{httpx.QueryParams({'refused': sentence})}", FOUND)
