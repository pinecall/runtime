"""Tests for the fleet doors: the heartbeat and LiveKit's word on a room's people."""

import json
import time

from livekit import api

from pinecall.channels.telephony.hand_over import LEG_PREFIX
from pinecall.domain.scope import Scope
from pinecall.log.store import Claim
from pinecall.wire.rest.fleet import HeartbeatRequest
from tests.conftest import AGENT, LIVEKIT_KEY, Knocking, a_worker_heard, postgres
from tests.fakes.livekit import Server, signed
from tests.gateway.api.conftest import a_call

WEBHOOK = "/v1/livekit/webhook"


@postgres
async def test_a_heartbeat_reaches_the_roster_of_its_fleet(knocking: Knocking) -> None:
    beat = HeartbeatRequest(
        fleet="pinecall-sandbox", worker="w1", active=1, max_jobs=4, load=0.2, draining=False
    )
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        existing = (await worker.post("/v1/fleet/heartbeat", json=beat.written())).json()
    totals = knocking.gateway.roster.totals("pinecall-sandbox", time.time())
    assert existing == {"cordoned": False, "full": False}
    assert (totals.fleet, totals.workers, totals.free) == ("pinecall-sandbox", 1, 3)


@postgres
async def test_a_tenants_key_opens_no_fleet_door(knocking: Knocking) -> None:
    beat = HeartbeatRequest(
        fleet="pinecall", worker="w1", active=0, max_jobs=None, load=0.1, draining=False
    )
    async with knocking.http(knocking.app["production"]) as tenant:
        refused = await tenant.post("/v1/fleet/heartbeat", json=beat.written())
    assert refused.status_code == 403


def agent_lost(call: str) -> str:
    """The body livekit sends when a worker's agent vanished from the call's room."""
    return json.dumps(
        {
            "event": "participant_left",
            "id": "EV_1",
            "room": {"name": call},
            "participant": {
                "identity": "agent-AJ_1",
                "kind": "AGENT",
                "disconnectReason": "CONNECTION_TIMEOUT",
            },
        }
    )


@postgres
async def test_livekit_saying_an_agent_was_lost_ends_its_call_and_sends_the_fleet(
    knocking: Knocking,
) -> None:
    context = a_call(knocking)
    store = knocking.gateway.logs.store
    await store.claim(
        context.call, AGENT, knocking.org.id, Claim(Scope(knocking.org.id, "sandbox"))
    )
    await store.append(context.call, AGENT, "call.started", {}, ephemeral=False)
    server = knocking.gateway.connections.servers["sandbox"]
    assert isinstance(server, Server)
    server.rooms.people = {context.call}
    a_worker_heard(knocking.gateway.roster)
    body = agent_lost(context.call)
    async with knocking.http("unused") as livekit:
        answered = await livekit.post(
            WEBHOOK,
            params={"world": "sandbox"},
            content=body,
            headers={"Authorization": signed(body, LIVEKIT_KEY)},
        )
    assert answered.status_code == 204
    assert [item.type for item in await store.whole(context.call)] == [
        "call.started",
        "call.ended",
    ]
    assert [(made.room, made.agent_name) for made in server.dispatcher.made] == [
        (context.call, "pinecall-sandbox/w1")
    ]


# The room of a sandbox call lives on the sandbox's LiveKit: production's is never asked of it.
@postgres
async def test_an_event_is_read_against_the_livekit_of_the_world_its_url_names(
    knocking: Knocking,
) -> None:
    context = a_call(knocking)
    store = knocking.gateway.logs.store
    await store.claim(
        context.call, AGENT, knocking.org.id, Claim(Scope(knocking.org.id, "sandbox"))
    )
    await store.append(context.call, AGENT, "call.started", {}, ephemeral=False)
    servers = knocking.gateway.connections.servers
    sandbox, production = servers["sandbox"], servers["production"]
    assert isinstance(sandbox, Server)
    assert isinstance(production, Server)
    sandbox.rooms.people = {context.call}
    body = agent_lost(context.call)
    signature = {"Authorization": signed(body, LIVEKIT_KEY)}
    async with knocking.http("unused") as livekit:
        unnamed = await livekit.post(WEBHOOK, content=body, headers=signature)
        nowhere = await livekit.post(
            WEBHOOK, params={"world": "staging"}, content=body, headers=signature
        )
    assert (unnamed.status_code, nowhere.status_code) == (204, 422)
    assert [item.type for item in await store.whole(context.call)] == ["call.started"]
    assert {type(request) for request in production.rooms.requests} == {api.ListParticipantsRequest}
    assert sandbox.rooms.requests == []


# A ring handed to the sandbox by SIP is two legs bridged in production's room, and no agent.
@postgres
async def test_the_leg_to_the_sandbox_hanging_up_closes_productions_room_and_its_caller(
    knocking: Knocking,
) -> None:
    server = knocking.gateway.connections.servers["production"]
    assert isinstance(server, Server)
    server.rooms.people = {"call-_+59899000001_abc"}
    body = json.dumps(
        {
            "event": "participant_left",
            "id": "EV_1",
            "room": {"name": "call-_+59899000001_abc"},
            "participant": {"identity": f"{LEG_PREFIX}m_ana", "kind": "SIP"},
        }
    )
    async with knocking.http("unused") as livekit:
        answered = await livekit.post(
            WEBHOOK, content=body, headers={"Authorization": signed(body, LIVEKIT_KEY)}
        )
    assert answered.status_code == 204
    assert [
        request.room
        for request in server.rooms.requests
        if isinstance(request, api.DeleteRoomRequest)
    ] == ["call-_+59899000001_abc"]
    assert server.rooms.people == set()


@postgres
async def test_an_event_livekit_did_not_sign_is_refused_and_changes_nothing(
    knocking: Knocking,
) -> None:
    server = knocking.gateway.connections.servers["production"]
    assert isinstance(server, Server)
    body = agent_lost("CA_1")
    forged = api.AccessToken(LIVEKIT_KEY, "not the box's secret, thirty-two bytes").to_jwt()
    async with knocking.http("unused") as livekit:
        unsigned = await livekit.post(WEBHOOK, content=body, headers={"Authorization": ""})
        wrong = await livekit.post(WEBHOOK, content=body, headers={"Authorization": forged})
    assert (unsigned.status_code, wrong.status_code) == (403, 403)
    assert server.dispatcher.made == []
