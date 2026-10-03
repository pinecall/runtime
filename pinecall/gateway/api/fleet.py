"""The fleet doors: a worker's heartbeat, and LiveKit's word on a room's people."""

import time

from fastapi import APIRouter, Request

from pinecall.channels import rooms
from pinecall.domain.errors import NotAllowed
from pinecall.gateway._deps import FleetKey, GatewayDep
from pinecall.gateway.dispatching.arrivals import arrived, settled
from pinecall.gateway.ending.stranded import stranded
from pinecall.wire.rest.fleet import HeartbeatRequest, HeartbeatResponse

router = APIRouter()

UNSIGNED = "the event carries no Authorization: LiveKit signs every event it sends"


@router.post("/v1/fleet/heartbeat")
async def heartbeat(
    body: HeartbeatRequest, _key: FleetKey, gateway: GatewayDep
) -> HeartbeatResponse:
    """A worker's report; the answer says whether it is cordoned and its fleet full."""
    return gateway.roster.report(body, time.time())


# livekit sends its token bare in Authorization, and every room event of the box: all but an
# agent lost mid-call are answered and let be.
@router.post("/v1/livekit/webhook", status_code=204)
async def receive_livekit_event(request: Request, gateway: GatewayDep) -> None:
    """A room event LiveKit signed: a caller alone is offered a worker; an agent lost, told."""
    # In this order: a worker gone sends the sentence, a caller alone is offered a worker, and a
    # room an agent joined or that ended is let go; each event moves one of the three.
    token = request.headers.get("Authorization")
    if not token:
        raise NotAllowed(UNSIGNED)
    settings = gateway.connections.settings
    body = (await request.body()).decode()
    event = rooms.livekit_event(
        body, token, settings.livekit_api_key or "", settings.livekit_api_secret or ""
    )
    await stranded(gateway.serving, gateway.offering, event)
    await arrived(gateway.offering, event)
    await settled(gateway.offering, event)
