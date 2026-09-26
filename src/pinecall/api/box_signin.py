"""The box-wide sign-in the operator wires: "Continue with Google" for every org's people."""

from __future__ import annotations

from fastapi import HTTPException, Request
from starlette.status import HTTP_204_NO_CONTENT

from pinecall._settings import Settings
from pinecall.api._box import BoxSettingsDep
from pinecall.api._deps import SettingsDep
from pinecall.api._gateway import where_this_gateway_answers
from pinecall.api._operator import an_operators_router
from pinecall.api.sso import UNREACHABLE, HttpDep
from pinecall.auth.openid import OpenIdRefused, configuration
from pinecall.orgs.box import SIGN_IN, BoxSettings
from pinecall.orgs.signin import GOOGLE, PROVIDERS, BoxSignIn
from pinecall.orgs.vault import NO_VAULT_KEY, NoVaultKey
from pinecall_protocol import WireModel
from pinecall_protocol.rest import BoxProvider
from pinecall_protocol.rest import BoxSignIn as SignInStanding

# The same gate every /v1/ops door takes. A box-wide provider is the BOX's: the client at Google
# is registered by whoever runs the machine, with this gateway's own callback, and an org that
# could wire one would be an org deciding how every other org's people sign in.
operator = an_operators_router()

# Where Google sends the person back. One string per provider, off the name this gateway is
# reached by: it is what the operator registers at Google, so it is in every answer.
CALLBACK = "/v1/login/{provider}/callback"

NOT_WIRED = "this box signs in with no {provider}: nothing to forget"

# A client id is not a secret, a client secret is. Both are replaced whole: the secret is
# write-only, so a change of the id carries it again.
EMPTY = "a client id and a client secret are both needed, and neither may be empty"


class WantedClient(WireModel):
    """What the operator brings from Google's console: the client this gateway is there."""

    client_id: str
    client_secret: str


@operator.get("/signin")
async def wired(box: BoxSettingsDep, settings: SettingsDep, request: Request) -> SignInStanding:
    """Every provider this box can offer, wired or not, with the URI to register at each."""
    base = where_this_gateway_answers(settings, request)
    return SignInStanding(google=await _standing(GOOGLE, box, base))


@operator.put("/signin/google")
async def wire_google(
    said: WantedClient, box: BoxSettingsDep, http: HttpDep, settings: SettingsDep, request: Request
) -> BoxProvider:
    """Wire "Continue with Google", replacing what was; 400 when Google's discovery does not
    answer, 503 on a box with no vault key to seal the secret."""
    client_id, client_secret = said.client_id.strip(), said.client_secret
    if not client_id or not client_secret:
        raise HTTPException(400, EMPTY)
    try:
        await configuration(http, PROVIDERS[GOOGLE].issuer)
    except OpenIdRefused as refused:
        raise HTTPException(400, UNREACHABLE.format(said=refused)) from refused
    try:
        await BoxSignIn(box).put(GOOGLE, client_id, client_secret)
    except NoVaultKey as unsealed:
        raise HTTPException(503, NO_VAULT_KEY) from unsealed
    return await _standing(GOOGLE, box, where_this_gateway_answers(settings, request))


@operator.delete("/signin/google", status_code=HTTP_204_NO_CONTENT)
async def unwire_google(box: BoxSettingsDep) -> None:
    """Forget it; the sign-in page offers no Google from the next load. 404 when none was wired."""
    if not await BoxSignIn(box).drop(GOOGLE):
        raise HTTPException(404, NOT_WIRED.format(provider=GOOGLE))


def where_the_provider_answers(settings: Settings, request: Request, provider: str) -> str:
    """The redirect URI this gateway is known by at that provider, as the operator registers it."""
    return f"{where_this_gateway_answers(settings, request)}{CALLBACK.format(provider=provider)}"


# `client_id` is read off the row even when the secret cannot be opened — a vault key rotated —
# so the page shows what was typed and `configured: false` says it is not usable, which is the
# one afternoon this shape has to explain.
async def _standing(name: str, box: BoxSettings, base: str) -> BoxProvider:
    """One provider as the operator sees it: whether it is usable, the client, the callback."""
    wired = await BoxSignIn(box).of(name)
    kept = await box.of(SIGN_IN.format(provider=name))
    return BoxProvider(
        configured=wired is not None,
        client_id=None if kept is None else str(kept.value.get("client_id") or "") or None,
        redirect_uri=f"{base}{CALLBACK.format(provider=name)}",
    )
