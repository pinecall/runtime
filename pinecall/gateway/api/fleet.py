"""The fleet doors: a worker's heartbeat, and LiveKit's word on a room's people."""

import time
from typing import Annotated

from fastapi import APIRouter, Query, Request

from pinecall.channels import rooms
from pinecall.channels.telephony.hand_over import unbridged
from pinecall.domain.errors import NotAllowed
from pinecall.domain.names import PRODUCTION, Env
from pinecall.fleet import worlds
from pinecall.fleet.demand import Line, wanted_scaled
from pinecall.gateway._deps import FleetKey, GatewayDep, webhook_body
from pinecall.gateway.dispatching.arrivals import arrived, settled
from pinecall.gateway.ending.stranded import stranded
from pinecall.wire.rest.fleet import HeartbeatRequest, HeartbeatResponse
from pinecall.wire.rest.ops import FleetDemand

router = APIRouter()

UNSIGNED = "the event carries no Authorization: LiveKit signs every event it sends"


@router.post("/v1/fleet/heartbeat")
async def heartbeat(
    body: HeartbeatRequest, _key: FleetKey, gateway: GatewayDep
) -> HeartbeatResponse:
    """A worker's report; the answer says whether it is cordoned and its fleet full."""
    return gateway.roster.report(body, time.time())


# KEDA's metrics-api scaler reads `wanted` and keeps the scaled Deployment at it (infra/charts),
# asking with its world's fleet key, which names the fleet: one number needs no operator's key.
@router.get("/v1/fleet/wanted")
async def wanted_workers(
    key: FleetKey,
    gateway: GatewayDep,
    scaled: Annotated[str, Query(min_length=1)],
    seats: Annotated[int, Query(gt=0)],
    most: Annotated[int, Query(gt=0)] = Line.at_most,
) -> FleetDemand:
    """How many workers whose names start with `scaled` the fleet wants, each of `seats` seats."""
    fleet = worlds.fleet_of(await worlds.fleets(gateway.connections.pool), key.env)
    line = Line(at_most=most, seats_per_worker=seats)
    now = time.time()
    wanted, active, capacity = wanted_scaled(gateway.roster.of(fleet, now), scaled, line, now)
    return FleetDemand(fleet=fleet, wanted=wanted, active=active, seats=capacity)


# livekit sends its token bare in Authorization, and every room event of the box: all but an
# agent lost mid-call are answered and let be. Each world's LiveKit names its world in the URL
# its config sends to, so the room is asked of the LiveKit it lives on.
@router.post("/v1/livekit/webhook", status_code=204)
async def receive_livekit_event(
    request: Request, gateway: GatewayDep, world: Annotated[Env, Query()] = PRODUCTION
) -> None:
    """A room event LiveKit signed: a caller alone is offered a worker; an agent lost, told."""
    # In this order: a worker gone sends the sentence, a caller alone is offered a worker, a room
    # an agent joined or that ended is let go, and a room a hand-over bridges goes with either of
    # its legs; each event moves one of the four.
    token = request.headers.get("Authorization")
    if not token:
        raise NotAllowed(UNSIGNED)
    settings = gateway.connections.settings
    body = (await webhook_body(request)).decode()
    event = rooms.livekit_event(
        body, token, settings.livekit_api_key or "", settings.livekit_api_secret or ""
    )
    await stranded(gateway.serving, gateway.offering, world, event)
    await arrived(gateway.offering, world, event)
    await settled(gateway.offering, event)
    await unbridged(gateway.offering.servers[world], event)
