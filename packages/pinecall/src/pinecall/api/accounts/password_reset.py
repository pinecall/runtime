"""POST /v1/login/reset: a forgotten password, asked for by the person who forgot it."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from starlette.status import HTTP_202_ACCEPTED

from pinecall.accounts import post_reset_link
from pinecall.api.accounts.identity import AtProduction
from pinecall.api.accounts.login import TOO_MANY, throttle_client
from pinecall.api.accounts.org_sso import SsoDep
from pinecall.api.deps import MembersDep, OrgsDep, SettingsDep, ThrottleDep
from pinecall.api.org.mail import OutboxDep
from pinecall.api.public_url import public_base_url
from pinecall_protocol import WireModel

# Production's alone (api/accounts/identity.py): a sandbox keeps no password and makes no person, so
# there every door here is 404, naming where people sign in.
router = APIRouter(dependencies=[AtProduction])


class Forgotten(WireModel):
    """The one thing this door takes: the address whose password was forgotten."""

    email: str


class ResetAsked(WireModel):
    """POST /v1/login/reset: nothing — 202, taken, whoever asked and whatever came of it."""


# 202, taken, and nothing else is ever said. Not whether anybody answers to the address, not
# whether their org can send mail, not whether their org signs in with a provider instead: a door
# that told them apart would be a door a stranger reads the box's directory out of, one address
# at a time. What a person who typed their own address sees is their inbox.
# No key: the person at this door has none, which is the whole of their problem. The throttle is
# the login's own — `POST /v1/login`, `POST /v1/login/orgs` and this one share a count per client
# and name — so a script walking a list of addresses is stopped at the sixth in a minute, and is
# stopped identically whoever the addresses belong to.
@router.post("/v1/login/reset", status_code=HTTP_202_ACCEPTED)
async def forgotten(
    said: Forgotten,
    request: Request,
    orgs: OrgsDep,
    members: MembersDep,
    throttle: ThrottleDep,
    sso: SsoDep,
    outbox: OutboxDep,
    settings: SettingsDep,
) -> ResetAsked:
    """Post a one-use reset link where one can be posted, and answer 202 whatever came of it."""
    if not throttle.allowed(f"{throttle_client(request)} */{said.email}"):
        raise HTTPException(429, TOO_MANY.format(email=said.email))
    base = public_base_url(settings, request)
    await post_reset_link(said.email, base, orgs, members, sso, outbox)
    return ResetAsked()
