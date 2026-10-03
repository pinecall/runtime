"""The fleet doors: a worker's heartbeat, how each fleet stands, and LiveKit saying one was lost."""

import time
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Request

from pinecall.channels import rooms
from pinecall.domain.errors import NotAllowed, NotAvailable, NotFound
from pinecall.domain.org import DEFAULT_ORG
from pinecall.domain.person import THE_FLEET, THE_JOIN
from pinecall.fleet import worlds
from pinecall.gateway._deps import FleetKey, GatewayDep, JoinKey, bearer_of, operator, public_url
from pinecall.gateway.dispatching.arrivals import arrived, settled
from pinecall.gateway.ending.stranded import stranded
from pinecall.tenancy import keys
from pinecall.tenancy.people import fingerprint
from pinecall.wire.rest.fleet import (
    HeartbeatRequest,
    HeartbeatResponse,
    JoinRequest,
    JoinResponse,
    JoinTokenRequest,
    JoinTokenResponse,
)

router = APIRouter()

UNSIGNED = "the event carries no Authorization: LiveKit signs every event it sends"

# A join token lives this long: a machine boots in two minutes, and one left in the open after
# that opens nothing.
JOIN_TOKEN_TTL_S = 600

NO_SUCH_FLEET = "no world's calls go to the fleet {fleet}: PUT /v1/ops/fleets names each world's"

NOT_THIS_MACHINE = "this token joins {name}, not {asked}: the loop made it for one machine"

NO_LIVEKIT_KEY = "this gateway holds no LiveKit key to hand a worker: it runs none"


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


# The loop asks for one before it makes a machine and hands it over as the machine's first-boot
# data, with the door to spend it at: the world's own name, where a key of that world opens.
@router.post("/v1/ops/fleet/join-tokens", dependencies=[Depends(operator)])
async def mint_join_token(
    body: JoinTokenRequest, request: Request, gateway: GatewayDep
) -> JoinTokenResponse:
    """A token good for one join of the fleet by a machine of that name, for ten minutes."""
    pool = gateway.connections.pool
    env = worlds.world_of(await worlds.fleets(pool), body.fleet)
    if env is None:
        raise NotFound(NO_SUCH_FLEET.format(fleet=body.fleet))
    expires_at = datetime.now(UTC) + timedelta(seconds=JOIN_TOKEN_TTL_S)
    issued = keys.Issued(
        org=DEFAULT_ORG,
        env=env,
        scopes=frozenset({THE_JOIN}),
        label=f"a join of {body.worker}",
        name=body.worker,
        expires_at=expires_at,
    )
    _, token = await keys.issue(pool, issued)
    url = gateway.connections.settings.address_of(env) or public_url(request, gateway)
    return JoinTokenResponse(token=token, url=url, expires_at=expires_at.timestamp())


# A machine's first boot knocks here with its token and gets what it runs on: a fleet key minted
# for it alone (revoked with the machine), the box's LiveKit pair and the store's secret, which
# worker.sh enroll seals on its disk. The token is spent in the same breath.
@router.post("/v1/fleet/join")
async def join_fleet(
    body: JoinRequest, key: JoinKey, request: Request, gateway: GatewayDep
) -> JoinResponse:
    """A worker machine's one join: its own fleet key, the LiveKit pair and the store's secret."""
    named = key.bearer.key.name
    if body.worker != named:
        raise NotAllowed(NOT_THIS_MACHINE.format(name=named, asked=body.worker))
    settings = gateway.connections.settings
    if not settings.livekit_api_key or not settings.livekit_api_secret:
        raise NotAvailable(NO_LIVEKIT_KEY)
    pool = gateway.connections.pool
    issued = keys.Issued(
        org=key.org,
        env=key.env,
        scopes=frozenset({THE_FLEET}),
        label=f"the {key.env} fleet, {body.worker}",
        name=body.worker,
    )
    _, worker_key = await keys.issue(pool, issued)
    spent = fingerprint(bearer_of(request.headers) or "")
    await keys.revoke(pool, spent)
    gateway.keys.forget(fingerprint=spent)
    return JoinResponse(
        fleet=worlds.fleet_of(await worlds.fleets(pool), key.env),
        worker_key=worker_key,
        livekit_api_key=settings.livekit_api_key,
        livekit_api_secret=settings.livekit_api_secret,
        s3_secret_access_key=settings.s3_secret_access_key,
    )


@router.delete("/v1/ops/fleet/{worker}/keys", status_code=204, dependencies=[Depends(operator)])
async def forget_worker_keys(worker: str, gateway: GatewayDep) -> None:
    """Revoke a machine's fleet key and any join token it never spent; the loop does, on delete."""
    for spent in await keys.revoke_named(gateway.connections.pool, DEFAULT_ORG, worker):
        gateway.keys.forget(fingerprint=spent)
