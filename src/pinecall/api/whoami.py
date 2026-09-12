"""Whose key knocked: the tenant's at /v1/whoami, and the box's own at /v1/ops/whoami."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from pinecall._version import __version__
from pinecall.api._deps import KeyDep, KeysDep, SettingsDep
from pinecall.api._operator import an_operator
from pinecall.auth.bearer import bearer_of
from pinecall.auth.keys import KeyRecord
from pinecall.types import Env
from pinecall_protocol import WireModel

router = APIRouter()

# The same gate every /v1/ops door takes. It carries no record — the ops key belongs to no org —
# so this door answers what the BOX is rather than whose the key is.
operator = APIRouter(prefix="/v1/ops", dependencies=[Depends(an_operator)])


class Whose(WireModel):
    """Who a key belongs to, as a terminal is allowed to read it: never the key, never its hash."""

    org: str
    key_id: str
    label: str | None = None
    # The world this key opens, and what it may do there: what a console gates its sections by.
    env: Env
    scopes: list[str]
    # The person the key was minted for, when it is a person's; an org's own key names nobody.
    subject: str | None = None
    name: str | None = None


# The door `pinecall login` proves a key at and `pinecall whoami` asks every day: it takes the key
# every other tenant door takes, and answers the words a person can check against their own.
@router.get("/v1/whoami")
async def whoami(key: KeyDep) -> Whose:
    """Whose key opened this door, where it opens, and what it may do."""
    return Whose(
        org=key.org,
        key_id=key.key_id,
        label=key.label,
        env=key.env,
        scopes=sorted(key.scopes),
        subject=key.subject,
        name=key.name,
    )


class TheBox(WireModel):
    """What the operator's page reads at login: that the key opened, and which box it opened."""

    # Always true: a key that did not open this door was answered 401 before the endpoint ran.
    # It is here so the page has a field to check rather than an empty body to guess at.
    operator: bool
    version: str
    # The domain this box answers on, so a person with two boxes open knows which tab is which.
    # None on a box that was told no domain — a laptop — and the page then says the URL it loaded.
    domain: str | None = None
    # Who is looking, when it is a person rather than the box's own key: their name and their org,
    # so the page's header says a name instead of a host. Null for the key out of the environment,
    # which belongs to nobody and is nobody.
    name: str | None = None
    org: str | None = None


# The door the operator's page proves its key at, exactly as the console proves a person's at
# /v1/whoami: a key that opens nothing is a page a person would trust tomorrow and a refusal they
# would not understand. It reads the settings and no table, because the ops key names no org.
@operator.get("/whoami")
async def the_box(settings: SettingsDep, keys: KeysDep, request: Request) -> TheBox:
    """That this key opens the operator's doors, which box they are, and who is holding it."""
    whose = await _whose(request, keys)
    return TheBox(
        operator=True,
        version=__version__,
        domain=settings.domain or None,
        name=None if whose is None else whose.name,
        org=None if whose is None else whose.org,
    )


# The gate already let this request through, so the only question left is which of the two ways it
# came: a person's key has a record, the box's own key has none and verifies as nothing.
async def _whose(request: Request, keys: KeysDep) -> KeyRecord | None:
    """The record behind the bearer, when it is a person's key rather than the box's own."""
    bearer = bearer_of(request.headers)
    if bearer is None:
        return None
    record = await keys.verify(bearer)
    return record if record is not None and record.subject is not None else None
