"""/v1/login/google: "Continue with Google", box-wide — a member of any org, by verified address."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from starlette.status import HTTP_302_FOUND

from pinecall.accounts import AccountRefused, claims_from_provider, home_of, provider_config
from pinecall.api.accounts.identity import AtProduction
from pinecall.api.accounts.login import throttle_client
from pinecall.api.accounts.org_sso import HandshakesDep, HttpDep, SsoDep
from pinecall.api.accounts.sso_login import (
    NO_CODE_BACK,
    NO_HANDSHAKE,
    PAIRING,
    THE_CONSOLE,
    THE_PROVIDER_SAID,
    TOO_MANY_SIGN_INS,
    landing_url,
    mint_sso_code,
)
from pinecall.api.deps import LoginCodesDep, MembersDep, SettingsDep, ThrottleDep
from pinecall.api.ops.box_settings import BoxSettingsDep
from pinecall.api.ops.box_signin import provider_redirect_uri
from pinecall.auth.members import normalize_email
from pinecall.auth.openid import authorization_url
from pinecall.orgs.box_signin import GOOGLE, BoxSignIn

# Production's alone (api/accounts/identity.py): a sandbox keeps no password and makes no person, so
# there every door here is 404, naming where people sign in.
router = APIRouter(dependencies=[AtProduction])

# A box-wide sign-in names no org: the handshake's org is this word, and only this door's
# callback spends a state opened with its provider (auth/sso_state.py, `provider`).
THE_BOX = ""

NOT_WIRED = "this box signs in with no Google: an operator wires one at PUT /v1/ops/signin/google"


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
    if not throttle.allowed(f"{throttle_client(request)} signin/{GOOGLE}"):
        raise HTTPException(429, TOO_MANY_SIGN_INS)
    provider = await provider_config(http, wired.issuer)
    redirect_uri = provider_redirect_uri(settings, request, GOOGLE)
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
        said = await claims_from_provider(
            http, wired.issuer, wired.client_id, wired.client_secret, handshake, code
        )
        home = await home_of(members, sso, normalize_email(said.email))
    except AccountRefused as refused:
        return _refused(str(refused))
    return RedirectResponse(
        landing_url(handshake.pairing, mint_sso_code(home, codes)), HTTP_302_FOUND
    )


def _refused(sentence: str) -> RedirectResponse:
    """Back to the sign-in page, with why, for a person to read."""
    return RedirectResponse(
        f"{THE_CONSOLE}?{httpx.QueryParams({'refused': sentence})}", HTTP_302_FOUND
    )
