"""Production says who a person is: the doors that are its alone, and a sandbox asking it."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException
from starlette.requests import HTTPConnection

from pinecall.api.accounts.org_sso import get_http
from pinecall.api.deps import SettingsDep
from pinecall.auth.identity import Identity
from pinecall.settings import Settings
from pinecall.types import PRODUCTION

# A sandbox instance keeps no password and makes no person: whoever signs in there signed in at
# production and carried a one-use code across. So the doors a person is made or proved at are
# production's, and on a sandbox they are not there at all — 404, with where they are.
SIGN_IN_THERE = "this is the sandbox, and people sign in at {identity}: it keeps no password here"


# A number bought for an org is paid on the box's own carrier account and attached to the box's own
# trunk (api/telephony/managed_numbers.py). Whether a sandbox may spend that account is not decided
# yet, so until it is, buying is production's too — the same 404, naming where it is done.
BOUGHT_THERE = "this door is production's: numbers are bought at {elsewhere}"


# One dependency, so a door says it is production's by what it declares and never by an `if`
# somebody could leave out of the next one. A plain function too, for the one door that is
# production's for one of its two bodies (POST /v1/login: a password, not a code).
def require_production(settings: SettingsDep) -> None:
    """Nothing at production; 404 naming where people sign in, anywhere else."""
    _only_at_production(settings, SIGN_IN_THERE.format(identity=settings.identity_url))


def buying_at_production(settings: SettingsDep) -> None:
    """Nothing at production; 404 naming where numbers are bought, anywhere else."""
    _only_at_production(settings, BOUGHT_THERE.format(elsewhere=settings.elsewhere_url))


def _only_at_production(settings: Settings, refusal: str) -> None:
    """The one test both say: this instance is production, or the door is not here."""
    if settings.world != PRODUCTION:
        raise HTTPException(404, refusal)


AtProduction = Depends(require_production)
BuysAtProduction = Depends(buying_at_production)


# ── a sandbox asking production who a person is ─────────────────────────────────


# Production, over the process's one httpx client, on a sandbox; None on production itself, whose
# own codes are the only ones there are — and which is not handed the client it would never use.
def get_identity(connection: HTTPConnection, settings: SettingsDep) -> Identity | None:
    """Who a sandbox asks who a person is, or None where this instance is the one asked."""
    if settings.world == PRODUCTION or settings.identity_url is None:
        return None
    return Identity(get_http(connection), settings.identity_url)


IdentityDep = Annotated["Identity | None", Depends(get_identity)]
