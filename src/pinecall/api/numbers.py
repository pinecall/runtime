"""The org's numbers: its carrier, what that account owns, one imported, one let go."""

# A number the box BUYS for the org is api/managed.py, which reuses the trunk steps written here.

from __future__ import annotations

import dataclasses
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from pinecall.api._deps import (
    KeptCarriersDep,
    NumbersKeyDep,
    RoutesDep,
    SettingsDep,
    TrunksDep,
    TwilioDep,
)
from pinecall.auth.keys import KeyRecord
from pinecall.routes.table import Routes
from pinecall.routes.trunks import NO_LIVEKIT, Trunks, fence_of
from pinecall.routes.twilio import (
    TwilioApi,
    TwilioNumber,
    TwilioRefused,
    origination_uri,
)
from pinecall.types import (
    Carrier,
    DeclarationRefused,
    Route,
    SipPeer,
    TwilioAccount,
    a_carrier_kind,
    a_sip_transport,
    an_env,
)
from pinecall.types.channel import CHANNELS_WITH_A_NUMBER, Channel
from pinecall_protocol import WireModel

router = APIRouter()

NO_BODY = 204

# The name of the org's trunk on ITS Twilio account: made once and found after, never doubled.
CARRIER_TRUNK = "pinecall-{org}"

NO_CARRIER = (
    "this org has no carrier yet: PUT /v1/carrier with a Twilio account or a SIP peer first"
)
NO_DOMAIN = "this gateway has no PINECALL_DOMAIN: a carrier cannot be pointed at a box with no name"
NOT_ON_ACCOUNT = "{number} is not a number of Twilio account {account}"
NOT_A_NUMBER_CHANNEL = "a number answers on phone or whatsapp, not {channel}"
NOT_VERIFIED = (
    "Twilio refused these credentials: the account SID, the key and the secret are checked"
)
NO_SUCH_NUMBER = "no number {number} in this org's world: nothing to let go"

# A move asks about the ORG's numbers and not the key's world, because crossing the two is what it
# is for; a number of another org is still nothing here.
NOT_THIS_ORGS = "no number {number} in this org: nothing to move"

# The number is already there. Not an error — it is the state that was asked for — but saying so
# is the difference between "done" and "done, and it was already done", which is what a person
# checking whether their move landed needs to read.
ALREADY_THERE = "{number} already answers in {env}"

# `?dry_run=true` is the plan and no writes: the very steps, in the very words, with the ids that
# stand today — what a person reads before letting the gateway touch a carrier account.
DRY_RUN = Query(False, description="print the plan and write nothing")


class WantedCarrier(WireModel):
    """What PUT /v1/carrier takes: which kind, and the credentials of that kind."""

    kind: str
    # Twilio: the account, and an API key pair or the auth token (`user` = the account SID then).
    account_sid: str | None = None
    user: str | None = None
    secret: str | None = None
    # SIP: what the peer registers with, and the networks its calls come from.
    username: str | None = None
    password: str | None = None
    addresses: list[str] = []
    # SIP, the other direction, every one of them optional: where the box places the INVITE, over
    # what, and what it authenticates as — the last two falling back to the pair above. A peer
    # that declares no outbound_host can be called FROM and never dialled THROUGH.
    outbound_host: str | None = None
    outbound_transport: str = "auto"
    outbound_username: str | None = None
    outbound_password: str | None = None


class WantedNumber(WireModel):
    """What POST /v1/numbers takes: which number, which agent answers it, on which channel."""

    number: str
    agent: str
    channel: str = "phone"


class WantedWorld(WireModel):
    """What PUT /v1/numbers/{number}/env takes: which world this number should answer in."""

    env: str


# ── the carrier ─────────────────────────────────────────────────────────────────


@router.put("/v1/carrier", status_code=NO_BODY)
async def bring(
    said: WantedCarrier, key: NumbersKeyDep, carriers: KeptCarriersDep, twilio: TwilioDep
) -> None:
    """This org's carrier, replacing whatever it had. A Twilio account is opened once to check."""
    carrier = _a_carrier(key.org, said)
    if isinstance(carrier.account, TwilioAccount):
        if await twilio(carrier.account).verified() is None:
            raise HTTPException(400, NOT_VERIFIED)
    await carriers.put(carrier)


@router.get("/v1/carrier")
async def brought(key: NumbersKeyDep, carriers: KeptCarriersDep) -> dict[str, Any]:
    """Which carrier the org brought, by kind and account — never a secret."""
    carrier = await carriers.of(key.org)
    if carrier is None:
        raise HTTPException(404, NO_CARRIER)
    return {"kind": carrier.kind, "account": carrier.named}


@router.delete("/v1/carrier", status_code=NO_BODY)
async def take_back(key: NumbersKeyDep, carriers: KeptCarriersDep) -> None:
    """Forget the carrier. Its numbers stay routed until each is let go."""
    if not await carriers.drop(key.org):
        raise HTTPException(404, NO_CARRIER)


# ── the numbers ─────────────────────────────────────────────────────────────────


# The same doors the worker is given, in the key's world — read with `numbers` and not with the
# worker's `app`, because a person who manages the org's numbers is not the process that answers
# them. Every door is a row: a class declares none, and the widget is not a door at all.
@router.get("/v1/numbers")
async def numbers(key: NumbersKeyDep, table: RoutesDep) -> list[dict[str, Any]]:
    """Every door the org answers in the key's world."""
    return [{"route": as_json(route)} for route in await table.of_org(key.org, key.env)]


@router.get("/v1/numbers/available")
async def available(
    key: NumbersKeyDep, carriers: KeptCarriersDep, twilio: TwilioDep, table: RoutesDep
) -> dict[str, Any]:
    """What the carrier account owns that this org has not imported yet, by number and name."""
    carrier = await carriers.of(key.org)
    if carrier is None:
        raise HTTPException(404, NO_CARRIER)
    if not isinstance(carrier.account, TwilioAccount):
        # A SIP peer owns what it owns; nobody here can list it. The import takes the number typed.
        return {"kind": "sip", "numbers": []}
    imported = {route.number for route in await table.of_org(key.org, key.env)}
    owned = await twilio(carrier.account).numbers()
    return {
        "kind": "twilio",
        "numbers": [
            {"number": one.number, "name": one.name, "imported": one.number in imported}
            for one in owned
        ],
    }


@router.post("/v1/numbers")
async def imported(
    said: WantedNumber,
    key: NumbersKeyDep,
    carriers: KeptCarriersDep,
    twilio: TwilioDep,
    trunks: TrunksDep,
    table: RoutesDep,
    settings: SettingsDep,
    dry_run: bool = DRY_RUN,
) -> dict[str, Any]:
    """One number into this org's world: the carrier's trunk, the SFU's trunk, the route."""
    route = a_route(key, said.number, said.agent, said.channel)
    carrier = await carriers.of(key.org)
    if carrier is None:
        raise HTTPException(404, NO_CARRIER)
    if not settings.domain:
        raise HTTPException(503, NO_DOMAIN)
    if trunks is None:
        raise HTTPException(503, NO_LIVEKIT)
    steps: list[str] = []
    try:
        if isinstance(carrier.account, TwilioAccount):
            api = twilio(carrier.account)
            owned = {one.number: one for one in await api.numbers()}
            if route.number not in owned:
                raise HTTPException(
                    404,
                    NOT_ON_ACCOUNT.format(number=route.number, account=carrier.account.account_sid),
                )
            name = CARRIER_TRUNK.format(org=carrier.org)
            account = carrier.account.account_sid
            await trunked(api, name, account, route, owned, settings.domain, steps, dry_run)
        await on_the_sfu(trunks, carrier, route, steps, dry_run)
    except TwilioRefused as refused:
        raise HTTPException(502, str(refused)) from refused
    return await routed(route, steps, table, dry_run)


async def routed(route: Route, steps: list[str], table: Routes, dry_run: bool) -> dict[str, Any]:
    """The last step of an import and of a purchase: the route row, and the answer with the plan."""
    steps.append(f"route    {route.number} {route.channel} → {route.agent} in {route.env}")
    if not dry_run:
        await table.put(route)
    return {"route": as_json(route), "steps": steps, "dry_run": dry_run}


# An org buys ONE number, and staging has to cost nothing: pointing it at the sandbox for an
# afternoon is how a team tries a new agent on the real line without a second number and a second
# bill. The carrier and the SFU are untouched — a number arrives at this box either way, and which
# world answers it is the row. Moving it back is the same verb with the other word.
@router.put("/v1/numbers/{number}/env")
async def moved(
    number: str, said: WantedWorld, key: NumbersKeyDep, table: RoutesDep
) -> dict[str, Any]:
    """This number answers in that world from the next call on. The org's number, either world."""
    try:
        world = an_env(said.env)
    except DeclarationRefused as refused:
        raise HTTPException(400, str(refused)) from refused
    route = await table.of_number(key.org, number)
    if route is None:
        raise HTTPException(404, NOT_THIS_ORGS.format(number=number))
    if route.env == world:
        return {
            "route": as_json(route),
            "moved": False,
            "said": ALREADY_THERE.format(number=number, env=world),
        }
    moved_to = dataclasses.replace(route, env=world)
    await table.put(moved_to)
    return {"route": as_json(moved_to), "moved": True, "from": route.env}


@router.delete("/v1/numbers/{number}", status_code=NO_BODY)
async def let_go(number: str, key: NumbersKeyDep, trunks: TrunksDep, table: RoutesDep) -> None:
    """The route gone and the number off the org's SFU trunk. The carrier account is not touched."""
    if not await table.remove(key.org, number):
        raise HTTPException(404, NO_SUCH_NUMBER.format(number=number))
    if trunks is not None:
        await trunks.released(key.org, number)


# ── the steps ───────────────────────────────────────────────────────────────────


# Each step is looked up before it is written, and the plan says which it found standing: a
# second run of an import that was interrupted must create nothing twice. Nothing is deleted.
async def trunked(
    api: TwilioApi,
    name: str,
    account: str,
    route: Route,
    owned: dict[str, TwilioNumber],
    domain: str,
    steps: list[str],
    dry: bool,
) -> None:
    """The trunk named so on the account, pointed at the box, with the number attached to it."""
    trunk = await api.trunk_named(name)
    if trunk is None:
        steps.append(f"trunk    {name} — created on account {account}")
        if not dry:
            trunk = await api.create_trunk(name)
    else:
        steps.append(f"trunk    {trunk.sid} {name} — standing")
    uri = origination_uri(domain)
    if trunk is None or uri not in trunk.origination:
        steps.append(f"origin   {uri} — set")
        if not dry and trunk is not None:
            await api.pointed_at(trunk, uri)
    else:
        steps.append(f"origin   {uri} — standing")
    on_it = () if trunk is None else await api.numbers_on(trunk.sid)
    if route.number in on_it:
        steps.append(f"number   {route.number} — on the trunk already")
    else:
        steps.append(f"number   {route.number} — attached to the trunk")
        if not dry and trunk is not None and route.number in owned:
            await api.attached(trunk.sid, owned[route.number].sid)


async def on_the_sfu(
    trunks: Trunks, carrier: Carrier, route: Route, steps: list[str], dry: bool
) -> None:
    """The org's inbound trunk on LiveKit with the number admitted, and its rule."""
    allowed, auth = fence_of(carrier)
    steps.append(
        f"livekit  inbound trunk pinecall-{carrier.org}: +{route.number}, from {len(allowed)} "
        f"networks{' with SIP auth' if auth else ''}; one room per caller"
    )
    # A dialled route always has one: a_route refused a channel without a number already.
    if not dry and route.number is not None:
        await trunks.admitted(carrier.org, route.number, allowed, auth)


def _a_carrier(org: str, said: WantedCarrier) -> Carrier:
    """The domain's Carrier out of the body, or a 400 in the domain's words."""
    try:
        if a_carrier_kind(said.kind) == "twilio":
            account_sid = said.account_sid or ""
            return Carrier(
                org=org,
                account=TwilioAccount(
                    account_sid=account_sid,
                    user=said.user or account_sid,
                    secret=said.secret or "",
                ),
            )
        return Carrier(
            org=org,
            account=SipPeer(
                username=said.username or "",
                password=said.password or "",
                addresses=tuple(said.addresses),
                outbound_host=said.outbound_host,
                outbound_transport=a_sip_transport(said.outbound_transport),
                outbound_username=said.outbound_username,
                outbound_password=said.outbound_password,
            ),
        )
    except DeclarationRefused as refused:
        raise HTTPException(400, str(refused)) from refused


def a_route(
    key: KeyRecord, number: str, agent: str, channel: str, *, managed: bool = False
) -> Route:
    """The route an import or a purchase ends in, refused before any account is touched."""
    if channel not in CHANNELS_WITH_A_NUMBER:
        raise HTTPException(400, NOT_A_NUMBER_CHANNEL.format(channel=channel))
    on: Channel = "phone" if channel == "phone" else "whatsapp"
    try:
        return Route(
            org=key.org, agent=agent, channel=on, number=number, env=key.env, managed=managed
        )
    except DeclarationRefused as refused:
        raise HTTPException(400, str(refused)) from refused


def as_json(route: Route) -> dict[str, Any]:
    """One route as the wire says it: every field of the domain's own Route."""
    return {
        "org": route.org,
        "agent": route.agent,
        "channel": route.channel,
        "number": route.number,
        "label": route.label,
        "env": route.env,
        "managed": route.managed,
    }
