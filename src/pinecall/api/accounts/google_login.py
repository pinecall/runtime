"""/v1/login/google: "Continue with Google", box-wide — a member of any org, by verified address."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from starlette.status import HTTP_302_FOUND

from pinecall.api.accounts.identity import AtProduction
from pinecall.api.accounts.login import only_with_the_provider, the_client
from pinecall.api.accounts.org_sso import HandshakesDep, HttpDep, SsoDep
from pinecall.api.accounts.sso_login import (
    NO_CODE_BACK,
    NO_HANDSHAKE,
    PAIRING,
    THE_CONSOLE,
    THE_PROVIDER_SAID,
    TOO_MANY_SIGN_INS,
    a_way_in,
    landing,
    the_provider,
    who_the_provider_says,
)
from pinecall.api.deps import LoginCodesDep, MembersDep, SettingsDep, ThrottleDep
from pinecall.api.ops.box_settings import BoxSettingsDep
from pinecall.api.ops.box_signin import where_the_provider_answers
from pinecall.auth.members import Members, normalize_email
from pinecall.auth.openid import authorization_url
from pinecall.orgs.box_signin import GOOGLE, BoxSignIn
from pinecall.orgs.org_sso import Sso
from pinecall.types import Member

# Production's alone (api/accounts/identity.py): a sandbox keeps no password and makes no person, so
# there every door here is 404, naming where people sign in.
router = APIRouter(dependencies=[AtProduction])

# A box-wide sign-in names no org: the handshake's org is this word, and only this door's
# callback spends a state opened with its provider (auth/sso_state.py, `provider`).
THE_BOX = ""

NOT_WIRED = "this box signs in with no Google: an operator wires one at PUT /v1/ops/signin/google"

# What the person is sent back to the sign-in page with when Google vouched for them and nobody
# here answers to the address. Said on the page and not as JSON, because it is a person in a
# browser mid-redirect who reads it — and it says what to do, which is to be invited.
NOBODY_HERE = (
    "{email} is not a member of any org on this box: an admin of your org has to invite that "
    "address before Google can sign it in"
)
DISABLED_EVERYWHERE = "{email} is disabled in every org of theirs here"
THEIR_OWN_PROVIDER = (
    "{email} belongs to an org that signs in with its own identity provider: open "
    "/v1/login/sso?org={org} instead"
)


@router.get("/v1/login/google")
async def sign_in(
    request: Request,
    box: BoxSettingsDep,
    handshakes: HandshakesDep,
    http: HttpDep,
    settings: SettingsDep,
    throttle: ThrottleDep,
    pairing: str | None = PAIRING,
) -> RedirectResponse:
    """302 to Google, carrying this sign-in's state, nonce and PKCE challenge; 404 unwired."""
    wired = await BoxSignIn(box).of(GOOGLE)
    if wired is None:
        raise HTTPException(404, NOT_WIRED)
    if not throttle.allowed(f"{the_client(request)} signin/{GOOGLE}"):
        raise HTTPException(429, TOO_MANY_SIGN_INS)
    provider = await the_provider(http, wired.issuer)
    redirect_uri = where_the_provider_answers(settings, request, GOOGLE)
    handshake = handshakes.open(THE_BOX, redirect_uri, pairing, provider=GOOGLE)
    return RedirectResponse(
        authorization_url(
            provider,
            wired.client_id,
            redirect_uri,
            state=handshake.state,
            nonce=handshake.nonce,
            verifier=handshake.verifier,
        ),
        status_code=HTTP_302_FOUND,
    )


# Every refusal past the handshake is a 302 back to the sign-in page with `?refused=<sentence>`,
# because the person is in a browser between two redirects and a JSON body there is a dead end.
# A dead state is the one 400: nothing says it was a person's, and a refresh makes another.
@router.get("/v1/login/google/callback")
async def back(
    state: str,
    box: BoxSettingsDep,
    handshakes: HandshakesDep,
    members: MembersDep,
    codes: LoginCodesDep,
    http: HttpDep,
    sso: SsoDep,
    code: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """The code exchanged, the address matched against every org's people, and a way in."""
    handshake = handshakes.spend(state)
    if handshake is None or handshake.provider != GOOGLE:
        raise HTTPException(400, NO_HANDSHAKE)
    if error is not None:
        return _refused(THE_PROVIDER_SAID.format(error=error))
    if not code:
        return _refused(NO_CODE_BACK)
    wired = await BoxSignIn(box).of(GOOGLE)
    if wired is None:
        return _refused(NOT_WIRED)
    try:
        said = await who_the_provider_says(
            http, wired.issuer, wired.client_id, wired.client_secret, handshake, code
        )
        home = await _the_person_home(members, sso, normalize_email(said.email))
    except HTTPException as refused:
        return _refused(str(refused.detail))
    return RedirectResponse(landing(handshake.pairing, a_way_in(home, codes)), HTTP_302_FOUND)


# The same rule the password login follows with no org named: the OLDEST org of theirs that a
# password would open — not disabled, not on its own provider — and the switcher does the rest. A
# row still INVITED is seated: Google verified the address, which is exactly what the link in the
# invitation would have proved, and it is what an org's own provider does
# (api/accounts/sso_login.py). Every invited row of theirs is seated, not only the oldest: they
# proved the address once.
async def _the_person_home(members: Members, sso: Sso | None, email: str) -> Member:
    """Their oldest org a Google sign-in may enter, seated where they were only invited."""
    rows = await members.orgs_of(email)
    if not rows:
        raise HTTPException(403, NOBODY_HERE.format(email=email))
    standing = [row for row in rows if row.status != "disabled"]
    if not standing:
        raise HTTPException(403, DISABLED_EVERYWHERE.format(email=email))
    allowed = [row for row in standing if not await only_with_the_provider(sso, row.org)]
    if not allowed:
        raise HTTPException(403, THEIR_OWN_PROVIDER.format(email=email, org=standing[0].org))
    # Google named the address, which is what proves it (0048): every standing row of theirs is
    # verified, and an invited one seated, on that word.
    seated: list[Member] = []
    for row in allowed:
        member = await members.vouched_for(row.org, row.id)
        seated.append(row if member is None else member)
    return seated[0]


def _refused(sentence: str) -> RedirectResponse:
    """Back to the sign-in page, with why, for a person to read."""
    return RedirectResponse(
        f"{THE_CONSOLE}?{httpx.QueryParams({'refused': sentence})}", HTTP_302_FOUND
    )
