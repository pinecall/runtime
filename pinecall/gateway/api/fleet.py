"""The fleet doors: a worker's heartbeat, and how each fleet stands."""

import time
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel

from pinecall.fleet import worlds
from pinecall.gateway._deps import (
    FleetKey,
    GatewayDep,
)
from pinecall.wire.rest.fleet import FleetTotals, HeartbeatRequest, HeartbeatResponse

router = APIRouter()


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
