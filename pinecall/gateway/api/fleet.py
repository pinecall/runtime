"""The fleet doors: a worker's heartbeat, how each fleet stands, and LiveKit saying one was lost."""

import time
from typing import Annotated

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from pinecall.channels import rooms
from pinecall.domain.errors import NotAllowed
from pinecall.fleet import worlds
from pinecall.gateway._deps import (
    FleetKey,
    GatewayDep,
)
from pinecall.gateway.ending.stranded import stranded
from pinecall.wire.rest.fleet import FleetTotals, HeartbeatRequest, HeartbeatResponse

router = APIRouter()

UNSIGNED = "the event carries no Authorization: LiveKit signs every event it sends"


class FleetQuery(BaseModel):
    """Which fleet the overflow asks about; unset, the fleet of the key's world."""

    fleet: str | None = None


@router.post("/v1/fleet/heartbeat")
async def heartbeat(
    body: HeartbeatRequest, _key: FleetKey, gateway: GatewayDep
) -> HeartbeatResponse:
    """A worker's report; the answer says whether it is cordoned and its fleet full."""
    return gateway.roster.report(body, time.time())


@router.get("/v1/fleet/standing")
async def fleet_status(
    key: FleetKey, gateway: GatewayDep, query: Annotated[FleetQuery, Query()]
) -> FleetTotals:
    """A fleet's workers summed; the overflow opens when it is full."""
    fleet = query.fleet or worlds.fleet_of(await worlds.fleets(gateway.connections.pool), key.env)
    return gateway.roster.totals(fleet, time.time())


# livekit sends its token bare in Authorization, and every room event of the box: all but an
# agent lost mid-call are answered and let be.
@router.post("/v1/livekit/webhook", status_code=204)
async def receive_livekit_event(request: Request, gateway: GatewayDep) -> None:
    """A room event LiveKit signed: an agent lost mid-call has its caller told, its call ended."""
    token = request.headers.get("Authorization")
    if not token:
        raise NotAllowed(UNSIGNED)
    settings = gateway.connections.settings
    body = (await request.body()).decode()
    event = rooms.livekit_event(
        body, token, settings.livekit_api_key or "", settings.livekit_api_secret or ""
    )
    await stranded(gateway.serving, gateway.connections.server, event)
