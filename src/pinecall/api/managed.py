"""A number the box buys for the org, on its own carrier account: the plan's stock of them."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from pinecall._settings import Settings
from pinecall.api._deps import (
    AdmissionDep,
    NumbersKeyDep,
    RoutesDep,
    SettingsDep,
    TrunksDep,
    TwilioDep,
)
from pinecall.api.numbers import DRY_RUN, NO_DOMAIN, a_route, on_the_sfu, routed, trunked
from pinecall.orgs.admission import QuotaExhausted
from pinecall.routes.trunks import NO_LIVEKIT
from pinecall.routes.twilio import BOX_TRUNK, TwilioNumber, TwilioRefused
from pinecall.types import Carrier, DeclarationRefused, TwilioAccount
from pinecall_protocol import WireModel

router = APIRouter()

NO_BOX_CARRIER = (
    "this gateway has no TWILIO_ACCOUNT_SID and TWILIO_API_SECRET: it buys numbers for nobody. "
    "Bring the org's own carrier with PUT /v1/carrier and import one instead"
)
NONE_FOR_SALE = "Twilio has no local voice number for sale in {where} right now"

# What the plan says the number is before it is bought: the search found it, nobody paid yet.
NOT_BOUGHT_YET = "<bought>"


class WantedPurchase(WireModel):
    """What POST /v1/numbers/buy takes: where the number should be from, and who answers it."""

    country: str
    area_code: str | None = None
    agent: str
    channel: str = "phone"


@router.post("/v1/numbers/buy")
async def bought(
    said: WantedPurchase,
    key: NumbersKeyDep,
    admission: AdmissionDep,
    twilio: TwilioDep,
    trunks: TrunksDep,
    table: RoutesDep,
    settings: SettingsDep,
    dry_run: bool = DRY_RUN,
) -> dict[str, Any]:
    """One number bought on the box's account into this org's world, if the plan has room."""
    account = the_boxs_account(settings)
    if account is None:
        raise HTTPException(503, NO_BOX_CARRIER)
    if not settings.domain:
        raise HTTPException(503, NO_DOMAIN)
    if trunks is None:
        raise HTTPException(503, NO_LIVEKIT)
    try:
        await admission.a_managed_number(key.org, said.agent, await table.managed_by(key.org))
    except QuotaExhausted as refused:
        raise HTTPException(429, str(refused)) from refused
    api = twilio(account)
    steps: list[str] = []
    try:
        number = await api.for_sale(said.country, said.area_code)
        if number is None:
            where = f"{said.country} {said.area_code}" if said.area_code else said.country
            raise HTTPException(404, NONE_FOR_SALE.format(where=where.strip()))
        route = a_route(key, number, said.agent, said.channel, managed=True)
        owned = TwilioNumber(sid=NOT_BOUGHT_YET, number=number, name=number)
        steps.append(f"buy      {number} — on account {account.account_sid}, billed to the box")
        if not dry_run:
            owned = await api.bought(number)
        sid = account.account_sid
        await trunked(api, BOX_TRUNK, sid, route, {number: owned}, settings.domain, steps, dry_run)
        boxs = Carrier(org=key.org, account=account)
        await on_the_sfu(trunks, settings.fleet, boxs, route, steps, dry_run)
    except TwilioRefused as refused:
        raise HTTPException(502, str(refused)) from refused
    return await routed(route, steps, table, dry_run)


def the_boxs_account(settings: Settings) -> TwilioAccount | None:
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
        raise HTTPException(503, f"the box's TWILIO_ACCOUNT_SID is refused: {refused}") from refused
