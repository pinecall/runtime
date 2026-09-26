"""/v1/login/sso: a person sent to their org's provider, and the way in when they return."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from starlette.status import HTTP_302_FOUND

from pinecall.accounts import A_BROWSER, claims_from_provider, provider_config, seat_vouched
from pinecall.api.accounts.identity import AtProduction
from pinecall.api.accounts.login import TOO_MANY, throttle_client
from pinecall.api.accounts.org_sso import (
    HandshakesDep,
    HttpDep,
    KeptSsoDep,
    SsoDep,
    idp_redirect_uri,
)
from pinecall.api.deps import (
    AdmissionDep,
    LoginCodesDep,
    MembersDep,
    OrgsDep,
    SettingsDep,
    ThrottleDep,
)
from pinecall.auth.keys import KeyRecord
from pinecall.auth.login_codes import NO_KEY_YET, LoginCodes
from pinecall.auth.members import normalize_email
from pinecall.auth.openid import authorization_url
from pinecall.types import SANDBOX, Member, parse_domain
from pinecall_protocol import WireModel
from pinecall_protocol.rest import SsoDiscovery, SsoOrg

# Production's alone (api/accounts/identity.py): a sandbox keeps no password and makes no person, so
# there every door here is 404, naming where people sign in.
router = APIRouter(dependencies=[AtProduction])


# Where the person lands with the word that mints their key. The console spends it at POST /v1/login
# {code} exactly as it spends the one `pinecall start` prints (auth/login_codes.py), so no key is
# ever in a URL — and the console needed no new screen to learn this.
THE_CONSOLE = "/"
# …or the card that signs a terminal in, when `pinecall login` is what sent them here. The
# pairing is untouched: they arrive at it holding a key, and approve the terminal as always.
THE_CARD = "/cli"

NO_SSO_HERE = "org {org} signs in with no identity provider"
NO_HANDSHAKE = (
    "no sign-in answers to that: it was finished already, it expired, or it never started"
)
ANOTHER_DOMAIN = "{email} is not in a domain {org} signs in with"

# The redirect mints a state and asks the provider for its configuration, so it is held to the
# same rate a sign-up is: per place, not per person, because nobody has typed an address yet.
# A whole office behind one address is five sign-ins a minute between them, and the sixth waits.
TOO_MANY_SIGN_INS = "too many sign-ins from here: try again in a minute"

# The query the provider sends back when the person said no, or when the client is wrong. Its own
# words, because `access_denied` and `invalid_client` are two very different afternoons.
THE_PROVIDER_SAID = "the sign-in was refused at the provider: {error}"
NO_CODE_BACK = "the provider sent no authorization code back"

ORG = Query(description="which org's provider signs this person in, by id or slug")
PAIRING = Query(default=None, description="the terminal waiting to be signed in, if one is")


class Wondering(WireModel):
    """What a sign-in page asks before anybody types a password: one address, nothing else."""

    email: str


@router.get("/v1/login/sso")
async def sign_in(
    request: Request,
    orgs: OrgsDep,
    sso: KeptSsoDep,
    handshakes: HandshakesDep,
    http: HttpDep,
    settings: SettingsDep,
    throttle: ThrottleDep,
    org: str = ORG,
    pairing: str | None = PAIRING,
) -> RedirectResponse:
    """302 to the org's provider, carrying this sign-in's state, nonce and PKCE challenge."""
    # One 404 whether the org is unwired or was never made: this door takes no key, and two
    # sentences would let a stranger walk the box's org slugs one request at a time.
    owner = await orgs.find(org)
    wired = None if owner is None else await sso.of(owner.id)
    if owner is None or wired is None:
        raise HTTPException(404, NO_SSO_HERE.format(org=org))
    if not throttle.allowed(f"{throttle_client(request)} sso/{owner.slug}"):
        raise HTTPException(429, TOO_MANY_SIGN_INS)
    provider = await provider_config(http, wired.issuer)
    redirect_uri = idp_redirect_uri(settings, request)
    handshake = handshakes.open(owner.id, redirect_uri, pairing)
    return RedirectResponse(
        authorization_url(
            provider,
            wired.client_id,
            redirect_uri,
            state=handshake.state,
            nonce=handshake.nonce,
            verifier=handshake.verifier,
        ),
        # A browser is redirected, twice: out to the provider, and back to the console it came
        # from. 302 both times, which is what every provider's own library sends and what a browser
        # does with the least surprise; nothing here answers a body a person would ever read.
        status_code=HTTP_302_FOUND,
    )


@router.get("/v1/login/sso/callback")
async def back(
    state: str,
    sso: KeptSsoDep,
    handshakes: HandshakesDep,
    orgs: OrgsDep,
    members: MembersDep,
    codes: LoginCodesDep,
    admission: AdmissionDep,
    http: HttpDep,
    code: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """The code exchanged, the id_token checked, the person seated, and a one-use way in."""
    handshake = handshakes.spend(state)
    if handshake is None:
        raise HTTPException(400, NO_HANDSHAKE)
    if error is not None:
        raise HTTPException(401, THE_PROVIDER_SAID.format(error=error))
    if not code:
        raise HTTPException(400, NO_CODE_BACK)
    wired = await sso.of(handshake.org)
    org = await orgs.find(handshake.org)
    # A state a box-wide provider's sign-in opened names no org, and is nobody's here.
    if wired is None or org is None or handshake.provider is not None:
        raise HTTPException(404, NO_SSO_HERE.format(org=handshake.org))
    said = await claims_from_provider(
        http, wired.issuer, wired.client_id, wired.client_secret, handshake, code
    )
    if not wired.admits(said.email):
        raise HTTPException(403, ANOTHER_DOMAIN.format(email=said.email, org=org.slug))
    member = await seat_vouched(org, wired, said, members, admission)
    return RedirectResponse(
        landing_url(handshake.pairing, mint_sso_code(member, codes)), HTTP_302_FOUND
    )


# No key at this door and no password in it: which orgs a person of this domain could sign in to
# with a provider. It says nothing about whether anybody answers to the address — a domain is a
# fact about the ORG's configuration — and it shares the login's throttle so it is not a way to
# walk the orgs of this box either.
@router.post("/v1/login/sso/discover")
async def discover(
    said: Wondering, request: Request, sso: SsoDep, orgs: OrgsDep, throttle: ThrottleDep
) -> SsoDiscovery:
    """The orgs an address of this domain signs in to with an identity provider, oldest first."""
    email = normalize_email(said.email)
    if not throttle.allowed(f"{throttle_client(request)} sso/{email}"):
        raise HTTPException(429, TOO_MANY.format(email=email))
    domain = parse_domain(email.rpartition("@")[2])
    # None is a box with no vault key, which can keep no client secret and so holds no provider
    # for anybody: an empty list is the truth there and never a refusal a sign-in page must read.
    found = () if sso is None or not domain else await sso.with_domain(domain)
    listed: list[SsoOrg] = []
    for wired in found:
        org = await orgs.find(wired.org)
        if org is not None:
            listed.append(SsoOrg(org=org.id, slug=org.slug, name=org.name))
    return SsoDiscovery(orgs=listed)


# The same word `pinecall start` prints and the console already knows how to spend, standing for a
# key that does not exist yet: the browser spending it is what mints one, labelled as a console's,
# the person's own and with what their role opens (accounts/signing_in.py:sign_in_with_code).
def mint_sso_code(member: Member, codes: LoginCodes) -> str:
    """A one-use login code for this person, good for five minutes and for one browser."""
    record = KeyRecord(
        key_id=NO_KEY_YET,
        org=member.org,
        label=A_BROWSER,
        env=SANDBOX,
        scopes=member.scopes,
        subject=member.id,
        name=member.name,
    )
    return codes.mint(record).code


def landing_url(pairing: str | None, code: str) -> str:
    """Where the browser ends up: the console, or the card that signs a terminal in."""
    if pairing is None:
        return f"{THE_CONSOLE}?{httpx.QueryParams({'login': code})}"
    return f"{THE_CARD}?{httpx.QueryParams({'c': pairing, 'login': code})}"
