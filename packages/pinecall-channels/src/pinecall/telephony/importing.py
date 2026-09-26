"""A number brought into an org's world: the carrier's trunk, the SFU's trunk, then the route."""

from __future__ import annotations

from dataclasses import dataclass, field

from pinecall.errors import PinecallError
from pinecall.orgs.carriers import NO_CARRIER, Carriers, NoCarrier
from pinecall.routes.inbound_trunks import NO_LIVEKIT, TRUNK_NAME, Trunks, fence_of
from pinecall.routes.records import Routes
from pinecall.routes.twilio import (
    CARRIER_TRUNK,
    TwilioApi,
    TwilioFor,
    TwilioNumber,
    origination_uri,
)
from pinecall.telephony.missing import NO_DOMAIN, NoDomain, NoMediaPlane
from pinecall.types import Carrier, Route, TwilioAccount

NOT_ON_ACCOUNT = "{number} is not a number of Twilio account {account}"
HELD_ELSEWHERE = (
    "{number} is already on the SFU's trunk {trunk}: a number answers on one trunk, so let it go"
    " there first"
)


class NotOnAccount(PinecallError):
    """The number is not one the org's Twilio account owns."""


class NumberHeldElsewhere(PinecallError):
    """The number already answers on another trunk of the SFU: one number, one trunk."""


@dataclass
class Routed:
    """What an import or a purchase did, one sentence a step, ending in the route."""

    route: Route
    steps: list[str] = field(default_factory=list[str])


# Each writes nothing under `dry`: the steps are the plan, in the words a real run would say.
async def import_number(
    route: Route,
    carriers: Carriers,
    twilio: TwilioFor,
    trunks: Trunks | None,
    routes: Routes,
    domain: str | None,
    fleet: str,
    *,
    dry: bool,
) -> Routed:
    """One number into this org's world: the carrier's trunk, the SFU's trunk, the route."""
    carrier = await carriers.of(route.org)
    if carrier is None:
        raise NoCarrier(NO_CARRIER)
    if not domain:
        raise NoDomain(NO_DOMAIN)
    if trunks is None:
        raise NoMediaPlane(NO_LIVEKIT)
    if route.number is not None and (other := await trunks.held_elsewhere(route.org, route.number)):
        raise NumberHeldElsewhere(HELD_ELSEWHERE.format(number=route.number, trunk=other))
    routed = Routed(route)
    if isinstance(carrier.account, TwilioAccount):
        api = twilio(carrier.account)
        owned = {one.number: one for one in await api.numbers()}
        if route.number not in owned:
            raise NotOnAccount(
                NOT_ON_ACCOUNT.format(number=route.number, account=carrier.account.account_sid)
            )
        name = CARRIER_TRUNK.format(fleet=fleet, org=carrier.org)
        account = carrier.account.account_sid
        await trunk_on_carrier(api, name, account, route, owned, domain, routed.steps, dry)
    await trunk_on_sfu(trunks, fleet, carrier, route, routed.steps, dry)
    await routed_on(routes, routed, dry=dry)
    return routed


async def routed_on(routes: Routes, routed: Routed, *, dry: bool) -> None:
    """The last step of an import and of a purchase: the route row."""
    route = routed.route
    routed.steps.append(f"route    {route.number} {route.channel} → {route.agent} in {route.env}")
    if not dry:
        await routes.put(route)


# Each step is looked up before it is written, and the plan says which it found standing: a
# second run of an import that was interrupted must create nothing twice. Nothing is deleted.
async def trunk_on_carrier(
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


async def trunk_on_sfu(
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
