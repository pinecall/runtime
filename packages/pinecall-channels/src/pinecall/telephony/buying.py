"""A number the box buys for an org, on its own carrier account: the plan's stock of them."""

from __future__ import annotations

from dataclasses import dataclass

from pinecall.errors import PinecallError
from pinecall.orgs.admission import Admission
from pinecall.routes.inbound_trunks import NO_LIVEKIT, Trunks
from pinecall.routes.records import Routes
from pinecall.routes.twilio import BOX_TRUNK, TwilioFor, TwilioNumber
from pinecall.settings import Settings
from pinecall.telephony.importing import Routed, routed_on, trunk_on_carrier, trunk_on_sfu
from pinecall.telephony.missing import (
    NO_BOX_CARRIER,
    NO_DOMAIN,
    NoBoxCarrier,
    NoDomain,
    NoMediaPlane,
)
from pinecall.types import Carrier, Channel, DeclarationRefused, Env, Route, TwilioAccount

NONE_FOR_SALE = "Twilio has no local voice number for sale in {where} right now"

# What the plan says the number is before it is bought: the search found it, nobody paid yet.
NOT_BOUGHT_YET = "<bought>"


class NoneForSale(PinecallError):
    """Twilio has no local voice number for sale where the org asked, right now."""


@dataclass(frozen=True)
class Buying:
    """Where the number should be from, and who answers it once it is the org's."""

    org: str
    env: Env
    agent: str
    channel: Channel
    country: str
    area_code: str | None = None


# `dry` buys nothing and writes nothing: the steps are the plan a real run would follow.
async def buy_number(
    buying: Buying,
    admission: Admission,
    routes: Routes,
    trunks: Trunks | None,
    twilio: TwilioFor,
    settings: Settings,
    *,
    dry: bool,
) -> Routed:
    """One number bought on the box's account into this org's world, if the plan has room."""
    account = box_twilio_account(settings)
    if account is None:
        raise NoBoxCarrier(NO_BOX_CARRIER)
    if not settings.domain:
        raise NoDomain(NO_DOMAIN)
    if trunks is None:
        raise NoMediaPlane(NO_LIVEKIT)
    await admission.a_managed_number(buying.org, buying.agent, await routes.managed_by(buying.org))
    api = twilio(account)
    number = await api.for_sale(buying.country, buying.area_code)
    if number is None:
        where = f"{buying.country} {buying.area_code}" if buying.area_code else buying.country
        raise NoneForSale(NONE_FOR_SALE.format(where=where.strip()))
    route = Route(
        org=buying.org,
        agent=buying.agent,
        channel=buying.channel,
        number=number,
        env=buying.env,
        managed=True,
    )
    routed = Routed(route)
    owned = TwilioNumber(sid=NOT_BOUGHT_YET, number=number, name=number)
    routed.steps.append(f"buy      {number} — on account {account.account_sid}, billed to the box")
    if not dry:
        owned = await api.bought(number)
    await trunk_on_carrier(
        api,
        BOX_TRUNK,
        account.account_sid,
        route,
        {number: owned},
        settings.domain,
        routed.steps,
        dry,
    )
    boxs = Carrier(org=buying.org, account=account)
    await trunk_on_sfu(trunks, settings.fleet, boxs, route, routed.steps, dry)
    await routed_on(routes, routed, dry=dry)
    return routed


def box_twilio_account(settings: Settings) -> TwilioAccount | None:
    """The box's own Twilio out of the settings, or None when the box was given none."""
    if not settings.twilio_account_sid or not settings.twilio_api_secret:
        return None
    try:
        return TwilioAccount(
            account_sid=settings.twilio_account_sid,
            user=settings.twilio_api_key or settings.twilio_account_sid,
            secret=settings.twilio_api_secret,
        )
    except DeclarationRefused as refused:
        raise NoBoxCarrier(f"the box's TWILIO_ACCOUNT_SID is refused: {refused}") from refused
