"""A call whose worker went away mid-call: its caller told once, a call back offered, drained."""

import logging

from livekit import api
from livekit.protocol.webhook import WebhookEvent

from pinecall.channels import rooms
from pinecall.channels.offers import Offering
from pinecall.channels.rooms import Dispatch
from pinecall.domain.names import Env
from pinecall.fleet import worlds
from pinecall.gateway._served import Serving
from pinecall.log import queries
from pinecall.log.queries import CallScope
from pinecall.wire.events import CallbackRequested, CallEnded

logger = logging.getLogger(__name__)

LEFT = "participant_left"

# The reasons livekit gives when a participant's connection was lost rather than closed: a worker
# killed, a machine gone, a job process that died. A worker that ends a call, drains or hands it on
# leaves CLIENT_INITIATED; a room deleted says so. livekit-server pkg/rtc/types ToDisconnectReason.
LOST = frozenset(
    {
        api.DisconnectReason.SIGNAL_CLOSE,
        api.DisconnectReason.CONNECTION_TIMEOUT,
        api.DisconnectReason.STATE_MISMATCH,
        api.DisconnectReason.JOIN_FAILURE,
        api.DisconnectReason.MEDIA_FAILURE,
        api.DisconnectReason.AGENT_ERROR,
    }
)

CALLBACK = "callback.requested"

STRANDED = "call %s: its worker went away (%s); the caller is told once and the call ends drained"


# The end is written first and only once: it is what tells a second delivery of the same event,
# the worker's own late end and the told job's own leaving that there is nothing left to do. The
# room is asked of the LiveKit of the world that sent the event.
async def stranded(
    serving: Serving, offering: Offering, world: Env, event: WebhookEvent
) -> str | None:
    """End the call whose agent the event says was lost, and send its fleet to tell the caller."""
    server = offering.servers[world]
    if not _an_agent_lost(event):
        return None
    call = event.room.name
    kept = await queries.scope_of_call(serving.connections.pool, call)
    if kept is None or kept.sealed or kept.scope is None:
        return None
    if not await rooms.left_alone(server, call):
        return None
    if not await _ended(serving, call, kept):
        return None
    await _call_back_offered(serving, call, kept.agent)
    scope = kept.scope
    fleet = worlds.fleet_of(await worlds.fleets(serving.connections.pool), scope.env)
    dispatch = Dispatch(
        agent=kept.agent,
        org=scope.org,
        env=scope.env,
        holder=scope.holder or None,
        worker_gone=True,
        entries_written=kept.written,
    )
    await offering.offer(call, fleet, dispatch)
    reason = api.DisconnectReason.Name(event.participant.disconnect_reason)
    logger.warning(STRANDED, call, reason)
    return call


def _an_agent_lost(event: WebhookEvent) -> bool:
    return (
        event.event == LEFT
        and event.participant.kind == api.ParticipantInfo.Kind.AGENT
        and event.participant.disconnect_reason in LOST
        and bool(event.room.name)
    )


# A log this process does not serve (it restarted since the call opened) is written and let go,
# as the reaper does: the told job's own entries then find no call here, and the reaper seals it.
async def _ended(serving: Serving, call: str, kept: CallScope) -> bool:
    now = serving.logs.store.clock()
    started = now if kept.started_at is None else kept.started_at
    ended = CallEnded(
        reason="drained",
        ended_by="platform",
        ended_at=now,
        duration_s=max(now - started, 0.0),
    )
    served = serving.live.calls.get(call)
    if served is not None:
        return await served.log.append_first("call.ended", ended.written()) is not None
    log = serving.logs.writing(call, kept.agent)
    try:
        return await log.append_first("call.ended", ended.written()) is not None
    finally:
        serving.logs.forget(call)


# Only a phone has a number to call back: the caller's, or on an outbound call the one dialled.
async def _call_back_offered(serving: Serving, call: str, agent: str) -> None:
    facts = (await queries.facts_of_calls(serving.connections.pool, [call])).get(call)
    if facts is None or facts.channel != "phone":
        return
    number = facts.from_number if facts.direction == "inbound" else facts.to_number
    if not number:
        return
    wanted = CallbackRequested(
        channel="phone", number=number, via="overflow", call=call, contact=None
    )
    await serving.logs.agent(agent).append(CALLBACK, wanted.written())
