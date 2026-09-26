"""A number the box buys for the org, on its own carrier account: the plan's stock of them."""

from __future__ import annotations

from fastapi import APIRouter

from pinecall.api.accounts.identity import BuysAtProduction
from pinecall.api.deps import (
    AdmissionDep,
    NumbersKeyDep,
    RoutesDep,
    SettingsDep,
    TrunksDep,
    TwilioDep,
)
from pinecall.api.telephony.numbers import DRY_RUN, NumberRouted, number_channel, wire_routed
from pinecall.telephony import Buying, buy_number
from pinecall_protocol import WireModel

router = APIRouter()


class WantedPurchase(WireModel):
    """What POST /v1/numbers/buy takes: where the number should be from, and who answers it."""

    country: str
    area_code: str | None = None
    agent: str
    channel: str = "phone"


@router.post("/v1/numbers/buy", dependencies=[BuysAtProduction])
async def bought(
    said: WantedPurchase,
    key: NumbersKeyDep,
    admission: AdmissionDep,
    twilio: TwilioDep,
    trunks: TrunksDep,
    table: RoutesDep,
    settings: SettingsDep,
    dry_run: bool = DRY_RUN,
) -> NumberRouted:
    """One number bought on the box's account into this org's world (telephony/buying.py)."""
    buying = Buying(
        org=key.org,
        env=key.env,
        agent=said.agent,
        channel=number_channel(said.channel),
        country=said.country,
        area_code=said.area_code,
    )
    routed = await buy_number(buying, admission, table, trunks, twilio, settings, dry=dry_run)
    return wire_routed(routed, dry_run)
