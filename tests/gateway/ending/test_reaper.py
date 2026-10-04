"""Tests for the reaper: the calls nothing runs any more are ended and sealed, and no other."""

import time
from datetime import date

from livekit import api

from pinecall.domain.agent import AgentConfig
from pinecall.domain.call import CallContext, Route, new_call_id
from pinecall.domain.scope import Scope
from pinecall.gateway._gateway import Gateway
from pinecall.gateway._served import opened, served_call
from pinecall.gateway.ending.reaper import reaped
from pinecall.gateway.ending.seal import sealed
from pinecall.log import openings
from pinecall.log.store import Claim
from pinecall.process.connections import closed
from pinecall.tenancy import orgs
from pinecall.wire.rest.calls import SealCallRequest
from tests.conftest import postgres
from tests.fakes.livekit import per_world
from tests.gateway.conftest import AGENT, OURS, a_call, a_start


@postgres
async def test_a_quiet_call_whose_room_has_no_agent_is_ended_as_drained_and_sealed(
    wired: Gateway,
) -> None:
    context = a_call(channel="phone")
    served = served_call(wired.serving, None, context, AgentConfig(slug=AGENT), OURS)
    await opened(served.log, context, AGENT)
    servers = per_world()
    reaped_now = await reaped(wired.serving, servers, 10_000.0)
    assert reaped_now == [context.call]
    kinds = [item.type for item in await wired.logs.store.whole(context.call)]
    assert kinds == ["call.ringing", "call.ended", "call.summary", "call.score"]
    await closed(servers)


# A sandbox call's room lives on the sandbox's LiveKit: production's says nothing of it.
@postgres
async def test_a_quiet_calls_room_is_asked_of_and_closed_on_its_worlds_livekit(
    wired: Gateway,
) -> None:
    context = a_call(channel="phone")
    await wired.logs.store.claim(context.call, AGENT, OURS.org, Claim(OURS))
    served = served_call(wired.serving, None, context, AgentConfig(slug=AGENT), OURS)
    await opened(served.log, context, AGENT)
    servers = per_world()
    servers["production"].rooms.existing = {context.call: True}
    assert await reaped(wired.serving, servers, 10_000.0) == [context.call]
    assert servers["production"].rooms.requests == []
    requests = [type(request) for request in servers["sandbox"].rooms.requests]
    assert requests == [api.ListRoomsRequest, api.DeleteRoomRequest]
    await closed(servers)


@postgres
async def test_a_call_with_an_agent_still_in_its_room_is_left_alone(wired: Gateway) -> None:
    context = a_call(channel="phone")
    served = served_call(wired.serving, None, context, AgentConfig(slug=AGENT), OURS)
    await opened(served.log, context, AGENT)
    servers = per_world()
    server = servers["sandbox"]
    server.rooms.existing = {context.call: True}
    assert await reaped(wired.serving, servers, 10_000.0) == []
    await closed(servers)


@postgres
async def test_a_call_that_only_just_went_quiet_or_ended_properly_is_never_reaped(
    wired: Gateway,
) -> None:
    quiet = a_call(channel="phone")
    over = a_call(channel="phone")
    for context in (quiet, over):
        served_call(wired.serving, None, context, AgentConfig(slug=AGENT), OURS)
        served = served_call(wired.serving, None, context, AgentConfig(slug=AGENT), OURS)
        await opened(served.log, context, AGENT)
    await sealed(
        wired.serving, wired.live.calls[over.call], SealCallRequest(usage=[], outcome="done")
    )
    servers = per_world()
    assert await reaped(wired.serving, servers, 100.0) == []
    assert await reaped(wired.serving, servers, 10_000.0) == [quiet.call]
    await closed(servers)


@postgres
async def test_a_worker_that_wrote_call_ended_and_died_is_finished_from_there(
    wired: Gateway,
) -> None:
    context = a_call(channel="phone")
    served = served_call(wired.serving, None, context, AgentConfig(slug=AGENT), OURS)
    await opened(served.log, context, AGENT)
    await served.log.append(
        "call.ended",
        {"reason": "caller_hung_up", "ended_by": "caller", "ended_at": 5.0, "duration_s": 30.0},
    )
    servers = per_world()
    assert await reaped(wired.serving, servers, 10_000.0) == [context.call]
    assert await reaped(wired.serving, servers, 10_000.0) == []
    kinds = [item.type for item in await wired.logs.store.whole(context.call)]
    assert kinds == ["call.ringing", "call.ended", "call.summary", "call.score"]
    summary = next(
        item for item in await wired.logs.store.whole(context.call) if item.type == "call.summary"
    )
    assert summary.data["reason"] == "caller_hung_up"
    await closed(servers)


@postgres
async def test_a_whatsapp_thread_nobody_runs_waits_its_two_hours_for_the_contact(
    wired: Gateway,
) -> None:
    route = Route(
        org="org_a", agent=AGENT, channel="whatsapp", number="+59829001199", env="sandbox"
    )
    context = CallContext(
        call=new_call_id(),
        channel="whatsapp",
        direction="inbound",
        caller="+59899123456",
        route=route,
        today=date(2026, 9, 28),
    )
    served = served_call(wired.serving, None, context, AgentConfig(slug=AGENT), OURS)
    await served.log.append("call.started", a_start(context))
    wired.live.close(context.call)
    servers = per_world()
    assert await reaped(wired.serving, servers, 1.0 + 10 * 60) == []
    assert await reaped(wired.serving, servers, 1.0 + 3 * 60 * 60) == [context.call]
    await closed(servers)


@postgres
async def test_a_written_call_this_process_runs_is_left_to_end_itself(wired: Gateway) -> None:
    context = a_call()
    served = served_call(wired.serving, None, context, AgentConfig(slug=AGENT), OURS)
    await served.log.append("call.started", a_start(context))
    servers = per_world()
    assert await reaped(wired.serving, servers, 10_000.0) == []
    wired.live.close(context.call)
    assert await reaped(wired.serving, servers, 10_000.0) == [context.call]
    await closed(servers)


# Under load the first entry waits on the writer: the claim is there, the ringing is not yet.
@postgres
async def test_a_call_claimed_whose_first_entry_has_not_landed_moved_when_it_opened(
    wired: Gateway,
) -> None:
    pool = wired.connections.pool
    org = await orgs.create(pool, "clinica-norte", "Clinica Norte")
    context = a_call(Scope(org.id, "sandbox"), channel="phone")
    await wired.logs.store.claim(context.call, AGENT, org.id)
    await openings.kept(pool, org.id, context, AgentConfig(slug=AGENT))
    servers = per_world()
    assert await reaped(wired.serving, servers, time.time()) == []
    assert await reaped(wired.serving, servers, time.time() + 10_000) == [context.call]
    await closed(servers)
