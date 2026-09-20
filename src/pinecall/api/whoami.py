"""Whose key knocked: the tenant's at /v1/whoami, and the box's own at /v1/ops/whoami."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from pinecall._version import __version__
from pinecall.api._deps import KeyDep, KeysDep, MembersDep, OrgsDep, SettingsDep
from pinecall.api._operator import an_operator, runs_the_box
from pinecall.auth.bearer import bearer_of
from pinecall.auth.keys import KeyRecord
from pinecall.auth.visiting import visiting
from pinecall.auth.world import a_person, opens_production
from pinecall.types import Env, is_a_deployment
from pinecall_protocol import WireModel

router = APIRouter()

# The same gate every /v1/ops door takes. It carries no record — the ops key belongs to no org —
# so this door answers what the BOX is rather than whose the key is.
operator = APIRouter(prefix="/v1/ops", dependencies=[Depends(an_operator)])


class Whose(WireModel):
    """Who a key belongs to, as a terminal is allowed to read it: never the key, never its hash."""

    org: str
    # The word the person types and reads — `clinica`, `pinecall` — as against `org`, which is the
    # id every other door takes. A line that says "org org_98889a61509c" to somebody is a line
    # that says nothing: the id is the machine's name for the tenant, never theirs. None only for
    # an org whose row is gone, where the id is all there is left to say.
    slug: str | None = None
    key_id: str
    label: str | None = None
    # The world this request runs in — a person's key names it with `pinecall-env`, a server's
    # token has its own — and what the key may do there: what a console gates its sections by.
    env: Env
    scopes: list[str]
    # The person the key was minted for, when it is a person's; an org's own key names nobody.
    subject: str | None = None
    name: str | None = None
    # Whether this person runs the BOX: their key opens /v1/ops as well, and the console's org
    # switch lists every org there is. False for a machine's key, which is nobody.
    operator: bool = False
    # True when they are inside an org they are no member of, as the operator (auth/visiting.py):
    # `subject` is then `operator:<their address>` and `name` still says who. A console reads it
    # to say so on the page, because what they do here is done in somebody else's org.
    visiting: bool = False
    # Whether this key may act in production: the person's row says so (an admin always), or it
    # is a production server's token. What `pinecall whoami` and the console's switch read.
    production: bool = False


# The door `pinecall login` proves a key at and `pinecall whoami` asks every day: it takes the key
# every other tenant door takes, and answers the words a person can check against their own.
@router.get("/v1/whoami")
async def whoami(key: KeyDep, orgs: OrgsDep, members: MembersDep, keys: KeysDep) -> Whose:
    """Whose key opened this door, where it opens, and what it may do."""
    org = await orgs.find(key.org)
    await keys.touch(key.key_id)
    return Whose(
        org=key.org,
        slug=None if org is None else org.slug,
        key_id=key.key_id,
        label=key.label,
        env=key.env,
        scopes=sorted(key.scopes),
        subject=key.subject,
        name=key.name,
        operator=await runs_the_box(key, members),
        visiting=visiting(key.subject) is not None,
        production=await opens_production(key, members)
        if a_person(key)
        else is_a_deployment(key.env),
    )


class TheBox(WireModel):
    """What the console's Box screens read: that the key opened, and which box it opened."""

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


# The door the console's Box screens prove an operator's key at, as the console proves a person's at
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
