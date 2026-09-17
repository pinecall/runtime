"""The org's identity provider: what an admin wires, what a reader may see, and the break-glass."""

from __future__ import annotations

from dataclasses import replace
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.requests import HTTPConnection

from pinecall._settings import Settings
from pinecall.api._deps import OrgsDep, SettingsDep, TeamKeyDep, an_org, held
from pinecall.api._gateway import where_this_gateway_answers
from pinecall.api._operator import an_operator
from pinecall.api.orgs import NO_BODY
from pinecall.auth.openid import OpenIdRefused, configuration
from pinecall.auth.sso import Handshakes
from pinecall.orgs.sso import Sso
from pinecall.orgs.vault import NO_VAULT_KEY
from pinecall.types import DeclarationRefused, OrgSso, a_domain, a_role
from pinecall_protocol import WireModel

# The org's own three doors, on a key with `team` — the same scope that invites a person and
# changes their role, because wiring the IdP is saying who the org's people ARE.
router = APIRouter()

# The same gate every /v1/ops door takes. The operator may READ which IdP an org is wired to and
# turn `required` off, and may change nothing else: the client, the secret and the domains are
# the tenant's, and an operator who could set them could sign in as anybody in that org.
operator = APIRouter(prefix="/v1/ops", dependencies=[Depends(an_operator)])

# Where the IdP sends the person back. It is the ONE string this gateway is known by at the
# provider: an admin registers it there by hand, so it is in every answer of these doors rather
# than in a page that would drift the day the box was reached by another name.
CALLBACK = "/v1/login/sso/callback"

# Nothing is wired. 404 and not an empty 200: `orgs sso rm` on an org that has none must never
# read as done, the same rule every other drop of this runtime follows.
NO_SSO = "this org signs in with no identity provider"
NO_SSO_THERE = "org {org} signs in with no identity provider"

# The one thing this door checks about the issuer before it keeps it: that somebody answers at
# it. An admin who pasted a tenant URL with a typo finds out here, in the console, and not on
# the first person who tries to sign in.
UNREACHABLE = "{said} — nothing was kept"


# The three things a sign-in is handed, beside the class each is of — which is where a dep lives
# when it is not the whole process's (api/_deps.py says so). The table is None on a box with no
# vault key, exactly as the vault and the carriers are, and the door then answers 503.
def the_sso(connection: HTTPConnection) -> Sso | None:
    """The org_sso table, or None when this runtime was given no vault key to seal a secret."""
    sso: Sso | None = getattr(connection.app.state, "sso", None)
    return sso


def the_handshakes(connection: HTTPConnection) -> Handshakes:
    """The sign-ins out at an identity provider right now, waiting for their callback."""
    return held(connection, "handshakes", Handshakes)


# The process's one httpx client, the very one Meta's Graph and the embedder ride. A door that
# opened a client of its own would open one per request, and a sign-in is two calls to somebody
# else's server with a person waiting on the redirect.
def the_http(connection: HTTPConnection) -> httpx.AsyncClient:
    """What this gateway talks to other people's servers with."""
    return held(connection, "http", httpx.AsyncClient)


SsoDep = Annotated["Sso | None", Depends(the_sso)]
HandshakesDep = Annotated[Handshakes, Depends(the_handshakes)]
HttpDep = Annotated[httpx.AsyncClient, Depends(the_http)]


# An org's client secret is a secret exactly as a provider key is, sealed under the same vault
# key; a runtime with none cannot keep one, and says so in the vault's own sentence.
async def a_kept_sso(sso: SsoDep) -> Sso:
    """The org_sso table, or 503: this box has no vault key."""
    if sso is None:
        raise HTTPException(503, NO_VAULT_KEY)
    return sso


KeptSsoDep = Annotated[Sso, Depends(a_kept_sso)]


class WantedSso(WireModel):
    """What an admin wires: the provider, the client this gateway is at it, and who it admits."""

    issuer: str
    client_id: str
    # Write-only, always: no door of this runtime reads one back, so a change of anything else
    # carries it again. That is the same shape `orgs quota` and the carriers already have —
    # the configuration is replaced whole, and a secret that could be kept while the rest moved
    # would be a secret this box had no way to show anybody had rotated.
    client_secret: str
    domains: list[str]
    # The role an address nobody invited is seated with. Left out: nobody is auto-provisioned,
    # and an email with no member row is refused at the callback.
    role: str | None = None
    required: bool = False


class Required(WireModel):
    """Whether a password opens this org at all. The operator's door takes only this."""

    required: bool


@router.get("/v1/org/sso")
async def wired(key: TeamKeyDep, sso: KeptSsoDep, settings: SettingsDep, request: Request) -> Any:
    """What this org signs in with, and the URI to register at the provider. Never the secret."""
    return _standing(await sso.of(key.org), where_the_idp_answers(settings, request))


@router.put("/v1/org/sso")
async def wire(
    said: WantedSso,
    key: TeamKeyDep,
    sso: KeptSsoDep,
    http: HttpDep,
    settings: SettingsDep,
    request: Request,
) -> Any:
    """Wire this org to its provider, replacing what it had; 400 for an issuer nobody answers."""
    wanted = _a_configuration(said, key.org)
    try:
        await configuration(http, wanted.issuer)
    except OpenIdRefused as refused:
        raise HTTPException(400, UNREACHABLE.format(said=refused)) from refused
    await sso.put(wanted)
    return _standing(wanted, where_the_idp_answers(settings, request))


@router.delete("/v1/org/sso", status_code=NO_BODY)
async def unwire(key: TeamKeyDep, sso: KeptSsoDep) -> None:
    """Forget the provider; this org's people sign in with a password again, from the next try."""
    if not await sso.drop(key.org):
        raise HTTPException(404, NO_SSO)


@operator.get("/orgs/{named}/sso")
async def wired_there(
    named: str, orgs: OrgsDep, sso: KeptSsoDep, settings: SettingsDep, request: Request
) -> Any:
    """Which provider one org is wired to. The same answer the org reads, and the same silence."""
    org = await an_org(named, orgs)
    return _standing(await sso.of(org.id), where_the_idp_answers(settings, request))


# The break-glass, and the reason it is the BOX's and not the org's: `required` is the org saying
# a password opens it no longer, and the day the IdP stops answering the admin who would turn it
# off is the person locked out. Turning it back ON is the org's own door above — an operator who
# could would be an operator deciding how a tenant's people sign in.
@operator.put("/orgs/{named}/sso/required")
async def still_required(
    named: str,
    said: Required,
    orgs: OrgsDep,
    sso: KeptSsoDep,
    settings: SettingsDep,
    request: Request,
) -> Any:
    """Whether this org's people may still use a password. 404 when it is wired to nothing."""
    org = await an_org(named, orgs)
    wired_to = await sso.of(org.id)
    if wired_to is None:
        raise HTTPException(404, NO_SSO_THERE.format(org=org.slug))
    changed = replace(wired_to, required=said.required)
    await sso.put(changed)
    return _standing(changed, where_the_idp_answers(settings, request))


# The one string this gateway is known by at the provider, off the name it is reached by
# (api/_gateway.py) — never a header a caller sent, which would be a sign-in a stranger could
# redirect to themselves.
def where_the_idp_answers(settings: Settings, request: Request) -> str:
    """The redirect URI this gateway is known by, as it is registered at the provider."""
    return f"{where_this_gateway_answers(settings, request)}{CALLBACK}"


def _a_configuration(said: WantedSso, org: str) -> OrgSso:
    """The body as the domain's own shape, or 400 in the refusal's own words."""
    try:
        return OrgSso(
            org=org,
            issuer=said.issuer.strip(),
            client_id=said.client_id.strip(),
            client_secret=said.client_secret,
            domains=tuple(a_domain(domain) for domain in said.domains),
            role=None if said.role is None else a_role(said.role),
            required=said.required,
        )
    except DeclarationRefused as refused:
        raise HTTPException(400, str(refused)) from refused


# The client secret is not in here and there is no door that answers with one. What is worth
# knowing about a stored secret is that it is there, which `configured` says.
#
# One shape either way, every field always there: an org that wired nothing answers the empty
# value of each rather than leaving them out, so a page parses one envelope and not two
# (protocol/schema/rest.json, OrgSso).
def _standing(sso: OrgSso | None, redirect_uri: str) -> dict[str, Any]:
    """One configuration as every reader sees it: what it is wired to, and never the secret."""
    return {
        "configured": sso is not None,
        "issuer": None if sso is None else sso.issuer,
        "client_id": None if sso is None else sso.client_id,
        "domains": [] if sso is None else list(sso.domains),
        "role": None if sso is None else sso.role,
        "required": sso is not None and sso.required,
        "redirect_uri": redirect_uri,
    }
