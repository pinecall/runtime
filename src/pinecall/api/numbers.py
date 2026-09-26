"""The org's numbers: its carrier, what that account owns, one imported, one let go."""

# A number the box BUYS for the org is api/managed.py, which reuses the trunk steps written here.

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import Field
from starlette.status import HTTP_204_NO_CONTENT

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
from pinecall.routes.trunks import NO_LIVEKIT, TRUNK_NAME, Trunks, fence_of
from pinecall.routes.twilio import (
    CARRIER_TRUNK,
    TwilioApi,
    TwilioNumber,
    origination_uri,
)
from pinecall.types import (
    Carrier,
    CarrierKind,
    Route,
    SipPeer,
    TwilioAccount,
    a_carrier_kind,
    a_sip_transport,
)
from pinecall.types.channel import CHANNELS_WITH_A_NUMBER, Channel
from pinecall_protocol import WireModel

router = APIRouter()

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

# livekit-sip refuses an INVITE whose number two inbound trunks list, so a number another trunk on
# the SFU already carries — the other instance's, or one made under an older name — is refused
# here, before the carrier or the SFU is touched: importing it would silence it on both.
HELD_ELSEWHERE = (
    "{number} is already on the SFU's trunk {trunk}: a number answers on one trunk, so let it go"
    " there first"
)

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
    addresses: list[str] = Field(default_factory=list[str])
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


class CarrierBrought(WireModel):
    """What GET /v1/carrier says: the carrier's kind and the account it is, never a secret."""

    kind: CarrierKind
    account: str


class NumberOwned(WireModel):
    """One number the carrier account owns, and whether this org imported it in this world."""

    number: str
    name: str
    imported: bool


class NumbersAvailable(WireModel):
    """What GET /v1/numbers/available says: the account's numbers, by the kind of carrier."""

    kind: CarrierKind
    numbers: list[NumberOwned]


# The route rides as the domain's own Route: FastAPI serializes the dataclass from the annotation,
# every field of it, so the wire says exactly what the table holds.
class NumberDoor(WireModel):
    """One row of GET /v1/numbers: a door the org answers at, and nothing but its route."""

    route: Route


class NumberRouted(WireModel):
    """What an import and a purchase end in: the route, the plan's steps, and whether it wrote."""

    route: Route
    steps: list[str]
    dry_run: bool


# ── the carrier ─────────────────────────────────────────────────────────────────


@router.put("/v1/carrier", status_code=HTTP_204_NO_CONTENT)
async def bring(
    said: WantedCarrier, key: NumbersKeyDep, carriers: KeptCarriersDep, twilio: TwilioDep
) -> None:
    """This org's carrier, replacing whatever it had. A Twilio account is opened once to check."""
    carrier = _a_carrier(key.org, said)
    if (
        isinstance(carrier.account, TwilioAccount)
        and await twilio(carrier.account).verified() is None
    ):
        raise HTTPException(400, NOT_VERIFIED)
    await carriers.put(carrier)


@router.get("/v1/carrier")
async def brought(key: NumbersKeyDep, carriers: KeptCarriersDep) -> CarrierBrought:
    """Which carrier the org brought, by kind and account — never a secret."""
    carrier = await carriers.of(key.org)
    if carrier is None:
        raise HTTPException(404, NO_CARRIER)
    return CarrierBrought(kind=carrier.kind, account=carrier.named)


@router.delete("/v1/carrier", status_code=HTTP_204_NO_CONTENT)
async def take_back(key: NumbersKeyDep, carriers: KeptCarriersDep) -> None:
    """Forget the carrier. Its numbers stay routed until each is let go."""
    if not await carriers.drop(key.org):
        raise HTTPException(404, NO_CARRIER)


# ── the numbers ─────────────────────────────────────────────────────────────────


# The same doors the worker is given, in the key's world — read with `numbers` and not with the
# worker's `app`, because a person who manages the org's numbers is not the process that answers
# them. Every door is a row: a class declares none, and the widget is not a door at all.
@router.get("/v1/numbers")
async def numbers(key: NumbersKeyDep, table: RoutesDep) -> list[NumberDoor]:
    """Every door the org answers in the key's world."""
    return [NumberDoor(route=route) for route in await table.of_org(key.org, key.env)]


@router.get("/v1/numbers/available")
async def available(
    key: NumbersKeyDep, carriers: KeptCarriersDep, twilio: TwilioDep, table: RoutesDep
) -> NumbersAvailable:
    """What the carrier account owns that this org has not imported yet, by number and name."""
    carrier = await carriers.of(key.org)
    if carrier is None:
        raise HTTPException(404, NO_CARRIER)
    if not isinstance(carrier.account, TwilioAccount):
        # A SIP peer owns what it owns; nobody here can list it. The import takes the number typed.
        return NumbersAvailable(kind="sip", numbers=[])
    imported = {route.number for route in await table.of_org(key.org, key.env)}
    owned = await twilio(carrier.account).numbers()
    return NumbersAvailable(
        kind="twilio",
        numbers=[
            NumberOwned(number=one.number, name=one.name, imported=one.number in imported)
            for one in owned
        ],
    )


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
) -> NumberRouted:
    """One number into this org's world: the carrier's trunk, the SFU's trunk, the route."""
    route = a_route(key, said.number, said.agent, said.channel)
    carrier = await carriers.of(key.org)
    if carrier is None:
        raise HTTPException(404, NO_CARRIER)
    if not settings.domain:
        raise HTTPException(503, NO_DOMAIN)
    if trunks is None:
        raise HTTPException(503, NO_LIVEKIT)
    if route.number is not None and (other := await trunks.held_elsewhere(key.org, route.number)):
        raise HTTPException(409, HELD_ELSEWHERE.format(number=route.number, trunk=other))
    steps: list[str] = []
    if isinstance(carrier.account, TwilioAccount):
        api = twilio(carrier.account)
        owned = {one.number: one for one in await api.numbers()}
        if route.number not in owned:
            raise HTTPException(
                404,
                NOT_ON_ACCOUNT.format(number=route.number, account=carrier.account.account_sid),
            )
        name = CARRIER_TRUNK.format(fleet=settings.fleet, org=carrier.org)
        account = carrier.account.account_sid
        await trunked(api, name, account, route, owned, settings.domain, steps, dry_run)
    await on_the_sfu(trunks, settings.fleet, carrier, route, steps, dry_run)
    return await routed(route, steps, table, dry_run)


async def routed(route: Route, steps: list[str], table: Routes, dry_run: bool) -> NumberRouted:
    """The last step of an import and of a purchase: the route row, and the answer with the plan."""
    steps.append(f"route    {route.number} {route.channel} → {route.agent} in {route.env}")
    if not dry_run:
        await table.put(route)
    return NumberRouted(route=route, steps=steps, dry_run=dry_run)


@router.delete("/v1/numbers/{number}", status_code=HTTP_204_NO_CONTENT)
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
    trunks: Trunks, fleet: str, carrier: Carrier, route: Route, steps: list[str], dry: bool
) -> None:
    """The org's inbound trunk on LiveKit with the number admitted, and its rule."""
    allowed, auth = fence_of(carrier)
    trunk = TRUNK_NAME.format(fleet=fleet, org=carrier.org)
    steps.append(
        f"livekit  inbound trunk {trunk}: {route.number}, from {len(allowed)} "
        f"networks{' with SIP auth' if auth else ''}; one room per caller"
    )
    # A dialled route always has one: a_route refused a channel without a number already.
    if not dry and route.number is not None:
        await trunks.admitted(carrier.org, route.number, allowed, auth)


def _a_carrier(org: str, said: WantedCarrier) -> Carrier:
    """The domain's Carrier out of the body, or a 400 in the domain's words."""
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


def a_route(
    key: KeyRecord, number: str, agent: str, channel: str, *, managed: bool = False
) -> Route:
    """The route an import or a purchase ends in, refused before any account is touched."""
    if channel not in CHANNELS_WITH_A_NUMBER:
        raise HTTPException(400, NOT_A_NUMBER_CHANNEL.format(channel=channel))
    on: Channel = "phone" if channel == "phone" else "whatsapp"
    return Route(org=key.org, agent=agent, channel=on, number=number, env=key.env, managed=managed)
