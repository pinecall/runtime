"""Tests for the fleet doors: the heartbeat and the roster."""

import json

from livekit import api

from pinecall.domain.scope import Scope
from pinecall.log.store import Claim
from pinecall.wire.rest.fleet import HeartbeatRequest
from tests.conftest import (
    AGENT,
    LIVEKIT_KEY,
    Knocking,
    postgres,
)
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
        totals = (await worker.get("/v1/fleet/standing")).json()
    assert existing == {"cordoned": False, "full": False}
    assert (totals["fleet"], totals["workers"], totals["free"]) == ("pinecall-sandbox", 1, 3)


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
    server = knocking.gateway.connections.server
    assert isinstance(server, Server)
    server.rooms.people = {context.call}
    body = agent_lost(context.call)
    async with knocking.http("unused") as livekit:
        answered = await livekit.post(
            WEBHOOK, content=body, headers={"Authorization": signed(body, LIVEKIT_KEY)}
        )
    assert answered.status_code == 204
    assert [item.type for item in await store.whole(context.call)] == [
        "call.started",
        "call.ended",
    ]
    assert [made.room for made in server.dispatcher.made] == [context.call]


@postgres
async def test_an_event_livekit_did_not_sign_is_refused_and_changes_nothing(
    knocking: Knocking,
) -> None:
    server = knocking.gateway.connections.server
    assert isinstance(server, Server)
    body = agent_lost("CA_1")
    forged = api.AccessToken(LIVEKIT_KEY, "not the box's secret, thirty-two bytes").to_jwt()
    async with knocking.http("unused") as livekit:
        unsigned = await livekit.post(WEBHOOK, content=body, headers={"Authorization": ""})
        wrong = await livekit.post(WEBHOOK, content=body, headers={"Authorization": forged})
    assert (unsigned.status_code, wrong.status_code) == (403, 403)
    assert server.dispatcher.made == []
