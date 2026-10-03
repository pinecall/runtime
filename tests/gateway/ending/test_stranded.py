"""Tests for a call whose worker went away: told once, a call back offered, ended as drained."""

import asyncio
import time

import pytest
from livekit import api
from livekit.protocol.webhook import WebhookEvent

from pinecall.channels.rooms import read_dispatch
from pinecall.domain.agent import AgentConfig
from pinecall.domain.call import CallContext
from pinecall.domain.names import JsonObject
from pinecall.domain.scope import Scope
from pinecall.gateway._gateway import Gateway
from pinecall.gateway._served import Served, served_call
from pinecall.gateway.ending.reaper import reaped
from pinecall.gateway.ending.seal import sealed
from pinecall.gateway.ending.stranded import stranded
from pinecall.log.store import Claim
from pinecall.wire.rest.calls import BatchedEntry, SealCallRequest
from tests.conftest import an_offering, postgres
from tests.fakes.livekit import Server
from tests.gateway.conftest import AGENT, OURS, a_call, a_start

LOST = api.DisconnectReason.CONNECTION_TIMEOUT


async def stranding(wired: Gateway, server: Server, event: WebhookEvent) -> str | None:
    """What stranded makes of the event, sending the fleet through a dispatcher of one worker."""
    return await stranded(wired.serving, an_offering(wired.connections.pool, server), event)


def left(
    call: str,
    reason: api.DisconnectReason = LOST,
    kind: api.ParticipantInfo.Kind = api.ParticipantInfo.Kind.AGENT,
) -> WebhookEvent:
    """LiveKit saying a participant of kind left the call's room, for that reason."""
    seat = api.ParticipantInfo(identity="agent-AJ_1", kind=kind, disconnect_reason=reason)
    return WebhookEvent(event="participant_left", room=api.Room(name=call), participant=seat)


async def a_live_call(wired: Gateway, context: CallContext, scope: Scope = OURS) -> Served:
    """A phone call its worker opened and started, as the open door leaves it."""
    await wired.logs.store.claim(context.call, AGENT, scope.org, Claim(scope))
    served = served_call(wired.serving, None, context, AgentConfig(slug=AGENT), scope)
    await served.log.append("call.started", a_start(context))
    return served


def a_caller_alone(context: CallContext) -> Server:
    """The SFU of a room its caller still sits in, with no agent."""
    server = Server()
    server.rooms.people = {context.call}
    return server


async def kinds_of(wired: Gateway, call: str) -> list[str]:
    """The types of the call's durable entries, in order."""
    return [item.type for item in await wired.logs.store.whole(call)]


async def callbacks_of(wired: Gateway) -> list[str]:
    """The calls a call back was asked for on the agent's log."""
    entries = await wired.logs.store.whole(f"@{AGENT}")
    return [str(item.data["call"]) for item in entries if item.type == "callback.requested"]


@postgres
async def test_a_lost_agent_ends_the_call_drained_offers_a_call_back_and_sends_the_fleet(
    wired: Gateway,
) -> None:
    context = a_call(channel="phone")
    await a_live_call(wired, context)
    server = a_caller_alone(context)
    assert (
        await stranded(
            wired.serving, an_offering(wired.connections.pool, server), left(context.call)
        )
        == context.call
    )
    entries = await wired.logs.store.whole(context.call)
    assert [item.type for item in entries] == ["call.started", "call.ended"]
    assert (entries[-1].data["reason"], entries[-1].data["ended_by"]) == ("drained", "platform")
    assert await callbacks_of(wired) == [context.call]
    (sent,) = server.dispatcher.made
    carried = read_dispatch(sent.metadata)
    assert (sent.room, sent.agent_name) == (context.call, "pinecall-sandbox/w1")
    assert (carried.worker_gone, carried.agent, carried.org, carried.env) == (
        True,
        AGENT,
        "org_a",
        "sandbox",
    )
    assert carried.entries_written == 0
    await server.aclose()


# The told job's writer follows on from what the dead worker's took: the head's count.
@postgres
async def test_the_fleet_is_told_how_many_entries_the_dead_workers_writer_sent(
    wired: Gateway,
) -> None:
    context = a_call(channel="phone")
    served = await a_live_call(wired, context)
    entry = BatchedEntry(type="custom", data={"name": "x", "data": {}}, ts=time.time())
    await served.log.append_many([entry, entry], after=0)
    server = a_caller_alone(context)
    assert (
        await stranded(
            wired.serving, an_offering(wired.connections.pool, server), left(context.call)
        )
        == context.call
    )
    (sent,) = server.dispatcher.made
    assert read_dispatch(sent.metadata).entries_written == 2
    await server.aclose()


@postgres
@pytest.mark.parametrize(
    "reason",
    [
        # A call ended, a worker that drained, a ring handed to the sandbox: the agent said goodbye.
        api.DisconnectReason.CLIENT_INITIATED,
        # The told job, the overflow and the reaper delete the room.
        api.DisconnectReason.ROOM_DELETED,
        api.DisconnectReason.SERVER_SHUTDOWN,
        api.DisconnectReason.UNKNOWN_REASON,
    ],
)
async def test_an_agent_that_left_on_purpose_or_with_its_room_changes_nothing(
    wired: Gateway, reason: api.DisconnectReason
) -> None:
    context = a_call(channel="phone")
    await a_live_call(wired, context)
    server = a_caller_alone(context)
    assert (
        await stranded(
            wired.serving, an_offering(wired.connections.pool, server), left(context.call, reason)
        )
        is None
    )
    assert await kinds_of(wired, context.call) == ["call.started"]
    assert server.dispatcher.made == []
    await server.aclose()


@postgres
async def test_a_caller_leaving_is_not_an_agent_lost(wired: Gateway) -> None:
    context = a_call(channel="phone")
    await a_live_call(wired, context)
    server = a_caller_alone(context)
    event = left(context.call, kind=api.ParticipantInfo.Kind.SIP)
    assert await stranding(wired, server, event) is None
    assert server.dispatcher.made == []
    await server.aclose()


@postgres
async def test_an_event_delivered_twice_tells_the_caller_once(wired: Gateway) -> None:
    context = a_call(channel="phone")
    await a_live_call(wired, context)
    server = a_caller_alone(context)
    assert (
        await stranded(
            wired.serving, an_offering(wired.connections.pool, server), left(context.call)
        )
        == context.call
    )
    assert (
        await stranded(
            wired.serving, an_offering(wired.connections.pool, server), left(context.call)
        )
        is None
    )
    assert len(server.dispatcher.made) == 1
    assert await callbacks_of(wired) == [context.call]
    await server.aclose()


@postgres
async def test_the_same_event_delivered_twice_at_once_tells_the_caller_once(
    wired: Gateway,
) -> None:
    context = a_call(channel="phone")
    await a_live_call(wired, context)
    server = a_caller_alone(context)
    both = await asyncio.gather(
        stranding(wired, server, left(context.call)),
        stranding(wired, server, left(context.call)),
    )
    assert both.count(context.call) == 1
    assert both.count(None) == 1
    assert (await kinds_of(wired, context.call)).count("call.ended") == 1
    assert len(server.dispatcher.made) == 1
    await server.aclose()


@postgres
async def test_a_worker_that_wrote_its_own_end_before_it_was_lost_is_left_to_its_seal(
    wired: Gateway,
) -> None:
    context = a_call(channel="phone")
    served = await a_live_call(wired, context)
    ended: JsonObject = {
        "reason": "caller_hung_up",
        "ended_by": "caller",
        "ended_at": 5.0,
        "duration_s": 4.0,
    }
    await served.log.append("call.ended", ended)
    server = a_caller_alone(context)
    assert (
        await stranded(
            wired.serving, an_offering(wired.connections.pool, server), left(context.call)
        )
        is None
    )
    assert server.dispatcher.made == []
    await server.aclose()


@postgres
async def test_a_call_already_sealed_or_never_opened_changes_nothing(wired: Gateway) -> None:
    context = a_call(channel="phone")
    served = await a_live_call(wired, context)
    await sealed(wired.serving, served, SealCallRequest(usage=[], outcome="done"))
    server = a_caller_alone(context)
    assert (
        await stranded(
            wired.serving, an_offering(wired.connections.pool, server), left(context.call)
        )
        is None
    )
    assert (
        await stranded(
            wired.serving, an_offering(wired.connections.pool, server), left("CA_nobody_opened")
        )
        is None
    )
    assert server.dispatcher.made == []
    await server.aclose()


@postgres
async def test_a_room_with_an_agent_in_it_again_or_with_nobody_left_is_not_told(
    wired: Gateway,
) -> None:
    handed = a_call(channel="phone")
    empty = a_call(channel="phone")
    await a_live_call(wired, handed)
    await a_live_call(wired, empty)
    server = a_caller_alone(handed)
    # The sandbox's worker took the ring the production one handed it.
    server.rooms.existing = {handed.call: True}
    assert (
        await stranded(
            wired.serving, an_offering(wired.connections.pool, server), left(handed.call)
        )
        is None
    )
    assert await stranding(wired, server, left(empty.call)) is None
    assert server.dispatcher.made == []
    assert await kinds_of(wired, empty.call) == ["call.started"]
    await server.aclose()


@postgres
async def test_a_call_in_a_browser_is_told_and_ended_but_has_no_number_to_call_back(
    wired: Gateway,
) -> None:
    context = a_call()
    await a_live_call(wired, context)
    server = a_caller_alone(context)
    assert (
        await stranded(
            wired.serving, an_offering(wired.connections.pool, server), left(context.call)
        )
        == context.call
    )
    assert await callbacks_of(wired) == []
    assert len(server.dispatcher.made) == 1
    await server.aclose()


@postgres
async def test_a_production_call_sends_the_production_fleet(wired: Gateway) -> None:
    live = Scope("org_a", "production", "m_1")
    context = a_call(live, channel="phone")
    await a_live_call(wired, context, live)
    server = a_caller_alone(context)
    production = an_offering(wired.connections.pool, server, "pinecall")
    await stranded(wired.serving, production, left(context.call))
    (sent,) = server.dispatcher.made
    assert (sent.agent_name, read_dispatch(sent.metadata).holder) == ("pinecall/w1", "m_1")
    await server.aclose()


@postgres
async def test_the_told_jobs_seal_ends_the_call_as_drained(wired: Gateway) -> None:
    context = a_call(channel="phone")
    served = await a_live_call(wired, context)
    server = a_caller_alone(context)
    await stranding(wired, server, left(context.call))
    await sealed(wired.serving, served, SealCallRequest(usage=[], outcome="we will call you"))
    entries = await wired.logs.store.whole(context.call)
    assert [item.type for item in entries] == [
        "call.started",
        "call.ended",
        "call.summary",
        "call.score",
    ]
    assert entries[2].data["reason"] == "drained"
    assert context.call not in wired.live.calls
    await server.aclose()


@postgres
async def test_a_call_this_gateway_forgot_is_ended_and_the_reaper_seals_it_if_nobody_came(
    wired: Gateway,
) -> None:
    context = a_call(channel="phone")
    await a_live_call(wired, context)
    # The gateway restarted after the call opened, and the worker died before it said it again.
    wired.live.close(context.call)
    wired.logs.forget(context.call)
    server = a_caller_alone(context)
    assert (
        await stranded(
            wired.serving, an_offering(wired.connections.pool, server), left(context.call)
        )
        == context.call
    )
    assert wired.logs.opened(context.call) is None
    assert await reaped(wired.serving, server, 10_000.0) == [context.call]
    entries = await wired.logs.store.whole(context.call)
    assert [item.type for item in entries] == [
        "call.started",
        "call.ended",
        "call.summary",
        "call.score",
    ]
    assert entries[2].data["reason"] == "drained"
    await server.aclose()
