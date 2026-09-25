"""/v1/login/sso: a person sent to their org's provider, and the way in when they return."""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from pinecall.api._deps import (
    AdmissionDep,
    LoginCodesDep,
    MembersDep,
    OrgsDep,
    SettingsDep,
    ThrottleDep,
)
from pinecall.api.identity import AtProduction
from pinecall.api.login import A_BROWSER, DISABLED, TOO_MANY, the_client
from pinecall.api.sso import (
    HandshakesDep,
    HttpDep,
    KeptSsoDep,
    SsoDep,
    where_the_idp_answers,
)
from pinecall.auth.codes import NO_KEY_YET, LoginCodes
from pinecall.auth.keys import KeyRecord
from pinecall.auth.members import Members, NoSeatLeft, an_address
from pinecall.auth.openid import (
    Claims,
    OpenIdRefused,
    claims,
    configuration,
    exchange,
    where_to_send,
)
from pinecall.auth.sso import Handshake
from pinecall.orgs.admission import Admission, QuotaExhausted
from pinecall.types import SANDBOX, Member, Org, OrgSso, a_domain
from pinecall_protocol import WireModel

# Production's alone (api/identity.py): a sandbox keeps no password and makes no person, so
# there every door here is 404, naming where people sign in.
router = APIRouter(dependencies=[AtProduction])

# A browser is redirected, twice: out to the provider, and back to the console it came from. 302
# both times, which is what every provider's own library sends and what a browser does with the
# least surprise; nothing here answers a body a person would ever read.
FOUND = 302

# Where the person lands with the word that mints their key. The console spends it at
# POST /v1/login {code} exactly as it spends the one `pinecall start` prints (auth/codes.py), so
# no key is ever in a URL — and the console needed no new screen to learn this.
THE_CONSOLE = "/"
# …or the card that signs a terminal in, when `pinecall login` is what sent them here. The
# pairing is untouched: they arrive at it holding a key, and approve the terminal as always.
THE_CARD = "/cli"

NO_SSO_HERE = "org {org} signs in with no identity provider"
NO_HANDSHAKE = (
    "no sign-in answers to that: it was finished already, it expired, or it never started"
)
IDP_REFUSED = "{issuer} did not sign this person in: {said}"
NO_EMAIL = "{issuer} vouched for somebody it gave no email address for"
NOT_VERIFIED = "{issuer} has not verified {email}: nobody is seated on an unverified address"
ANOTHER_DOMAIN = "{email} is not in a domain {org} signs in with"
NOBODY_HERE = "nobody in {org} answers to {email}, and it seats nobody it was not told to"

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
    if not throttle.allowed(f"{the_client(request)} sso/{owner.slug}"):
        raise HTTPException(429, TOO_MANY_SIGN_INS)
    provider = await the_provider(http, wired.issuer)
    redirect_uri = where_the_idp_answers(settings, request)
    handshake = handshakes.open(owner.id, redirect_uri, pairing)
    return RedirectResponse(
        where_to_send(
            provider,
            wired.client_id,
            redirect_uri,
            state=handshake.state,
            nonce=handshake.nonce,
            verifier=handshake.verifier,
        ),
        status_code=FOUND,
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
    said = await who_the_provider_says(
        http, wired.issuer, wired.client_id, wired.client_secret, handshake, code
    )
    if not wired.admits(said.email):
        raise HTTPException(403, ANOTHER_DOMAIN.format(email=said.email, org=org.slug))
    member = await _seated(org, wired, said, members, admission)
    return RedirectResponse(landing(handshake.pairing, a_way_in(member, codes)), FOUND)


# No key at this door and no password in it: which orgs a person of this domain could sign in to
# with a provider. It says nothing about whether anybody answers to the address — a domain is a
# fact about the ORG's configuration — and it shares the login's throttle so it is not a way to
# walk the orgs of this box either.
@router.post("/v1/login/sso/discover")
async def discover(
    said: Wondering, request: Request, sso: SsoDep, orgs: OrgsDep, throttle: ThrottleDep
) -> dict[str, Any]:
    """The orgs an address of this domain signs in to with an identity provider, oldest first."""
    email = an_address(said.email)
    if not throttle.allowed(f"{the_client(request)} sso/{email}"):
        raise HTTPException(429, TOO_MANY.format(email=email))
    domain = a_domain(email.rpartition("@")[2])
    # None is a box with no vault key, which can keep no client secret and so holds no provider
    # for anybody: an empty list is the truth there and never a refusal a sign-in page must read.
    found = () if sso is None or not domain else await sso.with_domain(domain)
    listed: list[dict[str, Any]] = []
    for wired in found:
        org = await orgs.find(wired.org)
        if org is not None:
            listed.append({"org": org.id, "slug": org.slug, "name": org.name})
    return {"orgs": listed}


async def the_provider(http: httpx.AsyncClient, issuer: str) -> Any:
    """The issuer's configuration, or 502: the request was right and somebody else is down."""
    try:
        return await configuration(http, issuer)
    except OpenIdRefused as refused:
        raise HTTPException(502, str(refused)) from refused


# The same three steps for an org's own provider and for a box-wide one (api/login_google.py):
# which client this gateway is at the issuer is all that differs between them.
async def who_the_provider_says(
    http: httpx.AsyncClient,
    issuer: str,
    client_id: str,
    client_secret: str,
    handshake: Handshake,
    code: str,
) -> Claims:
    """The code spent and the id_token checked — signature, issuer, audience, expiry, nonce."""
    provider = await the_provider(http, issuer)
    try:
        id_token = await exchange(
            http,
            provider,
            client_id,
            client_secret,
            code,
            handshake.redirect_uri,
            handshake.verifier,
        )
        said = await claims(http, provider, id_token, client_id, handshake.nonce)
    except OpenIdRefused as refused:
        raise HTTPException(401, IDP_REFUSED.format(issuer=issuer, said=refused)) from refused
    if not said.email:
        raise HTTPException(401, NO_EMAIL.format(issuer=issuer))
    # An address the provider has not verified is an address somebody typed into a directory, and
    # seating on one is how a stranger becomes a member by claiming a colleague's email.
    if not said.email_verified:
        raise HTTPException(401, NOT_VERIFIED.format(issuer=issuer, email=said.email))
    return said


async def _seated(
    org: Org, wired: OrgSso, said: Claims, members: Members, admission: Admission
) -> Member:
    """The member this address names in this org — invited, seated or made, per the org's rule."""
    email = an_address(said.email)
    kept = await members.by_email(org.id, email)
    if kept is not None:
        member = kept.member
        if member.status == "disabled":
            raise HTTPException(403, DISABLED.format(email=member.email, org=org.slug))
        # A person who just proved who they are at their org's OWN provider has accepted their
        # invitation: the link would only buy them a password, and this org signs in without one.
        # They keep no password, so `required` costs them nothing and the row is simply active —
        # and verified, on the provider's word (0048), whatever it was before.
        return await _activated(members, org, member)
    if wired.role is None:
        raise HTTPException(403, NOBODY_HERE.format(org=org.slug, email=email))
    # A member is a seat whoever it was made by, so the plan is asked here exactly as the invite
    # door asks it — before the row, because a seat is a stock — in the quota's own sentence.
    try:
        await admission.a_seat(org.id, await members.seated(org.id))
        invited = await members.invite(
            org.id,
            email,
            said.name or email.partition("@")[0],
            wired.role,
            (),
            seats=(await admission.quotas_of(org.id)).seats,
        )
    except NoSeatLeft as full:
        # The write judged the seat again, under its lock, and refused: the same sentence.
        await admission.a_seat(full.org, full.seated)
        raise
    except QuotaExhausted as refused:
        raise HTTPException(429, str(refused)) from refused
    if invited is None:
        raise HTTPException(403, NOBODY_HERE.format(org=org.slug, email=email))
    return await _activated(members, org, invited.member)


async def _activated(members: Members, org: Org, member: Member) -> Member:
    """The row active and verified, with no password on it: the provider is how they sign in."""
    seated = await members.vouched_for(org.id, member.id)
    if seated is None:
        raise HTTPException(403, NOBODY_HERE.format(org=org.slug, email=member.email))
    return seated


# The same word `pinecall start` prints and the console already knows how to spend, standing for a
# key that does not exist yet: the browser spending it is what mints one, labelled as a console's,
# the person's own and with what their role opens (api/login.py:_with_a_code).
def a_way_in(member: Member, codes: LoginCodes) -> str:
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


def landing(pairing: str | None, code: str) -> str:
    """Where the browser ends up: the console, or the card that signs a terminal in."""
    if pairing is None:
        return f"{THE_CONSOLE}?{httpx.QueryParams({'login': code})}"
    return f"{THE_CARD}?{httpx.QueryParams({'c': pairing, 'login': code})}"
