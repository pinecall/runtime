"""Tests for the fleet doors: the heartbeat and the roster."""

import json

from livekit import api

from pinecall.domain.names import JsonObject
from pinecall.domain.scope import Scope
from pinecall.log.store import Claim
from pinecall.wire.rest.fleet import HeartbeatRequest
from tests.conftest import (
    AGENT,
    FLEETS,
    LIVEKIT_KEY,
    Knocking,
    postgres,
)
from tests.fakes.livekit import Server, signed
from tests.gateway.api.conftest import a_call
from tests.gateway.api.test_ops import THE_OPS_KEY, with_an_ops_key

WEBHOOK = "/v1/livekit/webhook"

JOIN_TOKENS = "/v1/ops/fleet/join-tokens"

JOIN = "/v1/fleet/join"


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


def a_beat(worker: str) -> JsonObject:
    """One idle heartbeat of a machine of the sandbox fleet."""
    beat = HeartbeatRequest(
        fleet=FLEETS["sandbox"], worker=worker, active=0, max_jobs=32, load=0.0, draining=False
    )
    return beat.written()


@postgres
async def test_a_join_token_is_spent_once_for_a_machines_own_fleet_key(knocking: Knocking) -> None:
    with_an_ops_key(knocking)
    async with knocking.http(THE_OPS_KEY) as operator:
        minted = await operator.post(
            JOIN_TOKENS, json={"fleet": FLEETS["sandbox"], "worker": "pinecall-worker-7"}
        )
        unknown = await operator.post(JOIN_TOKENS, json={"fleet": "nobodys", "worker": "w"})
    assert (minted.status_code, unknown.status_code) == (200, 404)
    token = minted.json()
    assert token["token"].startswith("pc_test_")
    assert token["url"].startswith("http")
    async with knocking.http(token["token"]) as machine:
        wrong = await machine.post(JOIN, json={"worker": "pinecall-worker-8"})
        joined = await machine.post(JOIN, json={"worker": "pinecall-worker-7"})
        again = await machine.post(JOIN, json={"worker": "pinecall-worker-7"})
        no_heartbeat = await machine.post("/v1/fleet/heartbeat", json=a_beat("pinecall-worker-7"))
    assert (wrong.status_code, joined.status_code) == (403, 200)
    assert (again.status_code, no_heartbeat.status_code) == (401, 401)
    given = joined.json()
    assert (given["fleet"], given["livekit_api_key"]) == (FLEETS["sandbox"], LIVEKIT_KEY)
    assert given["worker_key"].startswith("pc_test_")
    async with knocking.http(given["worker_key"]) as worker:
        heard = await worker.post("/v1/fleet/heartbeat", json=a_beat("pinecall-worker-7"))
        no_join = await worker.post(JOIN, json={"worker": "pinecall-worker-7"})
    assert (heard.status_code, no_join.status_code) == (200, 403)


@postgres
async def test_a_machines_keys_go_with_the_machine(knocking: Knocking) -> None:
    with_an_ops_key(knocking)
    async with knocking.http(THE_OPS_KEY) as operator:
        minted = await operator.post(
            JOIN_TOKENS, json={"fleet": FLEETS["sandbox"], "worker": "pinecall-worker-9"}
        )
    async with knocking.http(minted.json()["token"]) as machine:
        joined = await machine.post(JOIN, json={"worker": "pinecall-worker-9"})
    worker_key = joined.json()["worker_key"]
    async with knocking.http(THE_OPS_KEY) as operator:
        forgotten = await operator.delete("/v1/ops/fleet/pinecall-worker-9/keys")
        again = await operator.delete("/v1/ops/fleet/pinecall-worker-9/keys")
    async with knocking.http(worker_key) as worker:
        gone = await worker.post("/v1/fleet/heartbeat", json=a_beat("pinecall-worker-9"))
    assert (forgotten.status_code, again.status_code, gone.status_code) == (204, 204, 401)
