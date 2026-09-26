"""The fleet's doors over the real app: a heartbeat, the roster, a cordon, a peer's key."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import asdict

import httpx
import pytest

from pinecall._settings import Settings
from pinecall.api.agents.registry import Registry
from pinecall.api.ops.peers import get_sandbox_peer
from pinecall.auth.keys import MemoryKeys
from pinecall.fleet import Heartbeat
from pinecall.log.store import MemoryStore
from pinecall.routes.records import MemoryRoutes
from pinecall.types import DEFAULT_ORG, PRODUCTION, SANDBOX, THE_FLEET, Env, Route
from pinecall.types.dispatch import Handover
from pinecall.worker.gateway_client import Gateway
from pinecall.worker.gateway_http import GatewayRefused
from tests.api.conftest import A_KEY, A_RECORD, AGENT, over_the_asgi_app
from tests.api.peering import A_PEER_KEY, THERE, Scripting
from tests.api.talking import answering_in

pytestmark = pytest.mark.unit

OPS_FLEET = "/v1/ops/fleet"
CALLBACKS = "/v1/callbacks"


# The fleet knocks with the key pinecall-worker-key@.service mints — org default, the `fleet`
# scope — and that is not the tenant's key the rest of this suite holds, so one is issued here.
@pytest.fixture
async def fleet_gateway(wired: None, keys: MemoryKeys) -> AsyncIterator[Gateway]:  # noqa: ARG001
    """worker/gateway_client.py over the real app, knocking with the fleet's own key."""
    issued = await keys.issue(
        DEFAULT_ORG, "the worker on this box", scopes=frozenset({THE_FLEET, "app", "calls"})
    )
    http = over_the_asgi_app(f"Bearer {issued.key}")
    yield Gateway(http)
    await http.aclose()


async def test_a_default_org_key_without_the_fleet_scope_opens_no_fleet_door(
    wired: None,  # noqa: ARG001
    keys: MemoryKeys,
) -> None:
    """Org default is where the box's own keys land; being there is not being the worker."""
    issued = await keys.issue(DEFAULT_ORG, "somebody's laptop", scopes=frozenset({"app"}))
    http = over_the_asgi_app(f"Bearer {issued.key}")
    try:
        with pytest.raises(GatewayRefused, match="fleet scope"):
            await Gateway(http).heartbeat(beat("liar"))
    finally:
        await http.aclose()


def beat(worker: str, active: int = 0, max_jobs: int | None = 4) -> Heartbeat:
    return Heartbeat(
        worker=worker,
        active=active,
        max_jobs=max_jobs,
        load=active / (max_jobs or 1),
        draining=False,
    )


async def test_a_heartbeat_puts_the_worker_on_the_roster_the_operator_reads(
    fleet_gateway: Gateway, ops_http: httpx.AsyncClient
) -> None:
    standing = await fleet_gateway.heartbeat(beat("pinecall-worker-1", active=1))
    assert (standing.cordoned, standing.full) == (False, False)
    listed = (await ops_http.get(OPS_FLEET)).json()
    assert [one["worker"] for one in listed["workers"]] == ["pinecall-worker-1"]
    assert listed["totals"] == {
        "workers": 1,
        "active": 1,
        "seats": 4,
        "free": 3,
        "accepting": 1,
        "full": False,
        "busy": 0.25,
    }


async def test_a_tenants_key_opens_no_fleet_door(worker_gateway: Gateway) -> None:
    """A tenant that could post a heartbeat could invent a seat, or fill the fleet."""
    with pytest.raises(GatewayRefused, match="403"):
        await worker_gateway.heartbeat(beat("liar"))
    with pytest.raises(GatewayRefused, match="403"):
        await worker_gateway.fleet_is_full()


async def test_a_cordon_reaches_the_worker_on_its_next_heartbeat(
    fleet_gateway: Gateway, ops_http: httpx.AsyncClient
) -> None:
    await fleet_gateway.heartbeat(beat("pinecall-worker-1"))
    assert (await ops_http.post(f"{OPS_FLEET}/pinecall-worker-1/cordon")).status_code == 204
    assert (await fleet_gateway.heartbeat(beat("pinecall-worker-1"))).cordoned is True
    assert (await ops_http.delete(f"{OPS_FLEET}/pinecall-worker-1/cordon")).status_code == 204
    assert (await fleet_gateway.heartbeat(beat("pinecall-worker-1"))).cordoned is False


async def test_cordoning_a_name_nobody_has_is_a_404(ops_http: httpx.AsyncClient) -> None:
    answer = await ops_http.post(f"{OPS_FLEET}/nobody/cordon")
    assert answer.status_code == 404
    assert "nobody" in answer.json()["detail"]


async def test_the_fleet_is_full_when_every_worker_is_at_the_line(fleet_gateway: Gateway) -> None:
    assert await fleet_gateway.fleet_is_full() is False
    await fleet_gateway.heartbeat(beat("pinecall-worker-1", active=3, max_jobs=4))
    assert await fleet_gateway.fleet_is_full() is True
    await fleet_gateway.heartbeat(beat("pinecall-worker-2", active=1, max_jobs=4))
    assert await fleet_gateway.fleet_is_full() is False


async def test_a_callback_lands_on_the_agents_log_and_the_org_reads_it_back(
    worker_gateway: Gateway, registry: Registry, store: MemoryStore
) -> None:
    """The tenant's own key: the agent must be its org's, and the request is written as an event."""
    await registry.register(
        "app_1",
        A_RECORD.org,
        PRODUCTION,
        AGENT,
    )
    await worker_gateway.callback_requested(AGENT, "phone", "+34600000000", "call_1")
    written = await store.agent_since(AGENT)
    assert written[-1].type == "callback.requested"
    assert written[-1].data == {
        "channel": "phone",
        "number": "+34600000000",
        "via": "overflow",
        "call": "call_1",
        "contact": None,
    }
    http = over_the_asgi_app(f"Bearer {A_KEY}")
    try:
        page = (await http.get(CALLBACKS)).json()
    finally:
        await http.aclose()
    assert [one["number"] for one in page["requests"]] == ["+34600000000"]
    assert page["requests"][0]["agent"] == AGENT
    assert page["next"] is None


async def test_a_callback_for_an_agent_of_another_org_is_a_404(worker_gateway: Gateway) -> None:
    with pytest.raises(GatewayRefused, match="404"):
        await worker_gateway.callback_requested("somebody-elses", "phone", "+34600000000", None)


# ── the other instance's fleet key: where a production ring lands ────────────────

RINGS = f"/v1/agents/{AGENT}/rings-for"
BERNA = "m_berna"
BERNAS_PHONE = "+59899111111"
WHOSE = {"org": A_RECORD.org, "caller": BERNAS_PHONE}
# What `box peer` mints: a fleet key of the instance it is minted at, for the doors the other
# instance knocks at — the fleet's, which name the org they ask for, and `app`, which opens them.
PEER_SCOPES = frozenset({THE_FLEET, "app"})


async def a_peer_key(keys: MemoryKeys, minted_at: Env) -> str:
    """A key minted at that instance for the other one, as `box peer` mints it."""
    issued = await keys.issue(DEFAULT_ORG, "peer-for-the-other", env=minted_at, scopes=PEER_SCOPES)
    return issued.key


# The sandbox's side: the claims are its own, and so is the name of the fleet that builds the call.
async def test_the_sandbox_tells_production_whose_copy_takes_the_ring_and_which_fleet_builds_it(
    wired: None,  # noqa: ARG001
    settings: Settings,
    keys: MemoryKeys,
    registry: Registry,
) -> None:
    answering_in(SANDBOX, settings)
    await registry.register("app_bernas_laptop", A_RECORD.org, SANDBOX, AGENT, holder=BERNA)
    registry.calls_from(SANDBOX, BERNAS_PHONE, BERNA)

    async with over_the_asgi_app(f"Bearer {await a_peer_key(keys, SANDBOX)}") as production:
        said = await production.get(RINGS, params=WHOSE)

    assert said.json() == {"holder": BERNA, "fleet": "pinecall-sandbox"}


# A peer key is a fleet key OF ONE INSTANCE: the sandbox's opens the sandbox and nothing else.
async def test_a_key_the_sandbox_minted_opens_no_door_of_production(
    wired: None,  # noqa: ARG001
    keys: MemoryKeys,
) -> None:
    async with over_the_asgi_app(f"Bearer {await a_peer_key(keys, SANDBOX)}") as stray:
        refused = await stray.get(RINGS, params=WHOSE)

    assert refused.status_code == 403
    assert "made for sandbox" in refused.json()["detail"]


# Production's side: nothing in its own table, so it asks its sandbox — and the worker is told the
# corner and the fleet it hands the room to.
async def test_production_asks_its_sandbox_when_its_own_table_has_nobody(
    fleet_gateway: Gateway, other_instance: Scripting
) -> None:
    handing = {"holder": BERNA, "fleet": "pinecall-sandbox"}
    sandbox = other_instance(get_sandbox_peer, httpx.Response(200, json=handing))

    handover = await fleet_gateway.rings_for(AGENT, org=A_RECORD.org, caller=BERNAS_PHONE)

    assert handover == Handover(holder=BERNA, fleet="pinecall-sandbox")
    (asked,) = sandbox.asked
    assert (asked.url.path, dict(asked.url.params)) == (RINGS, WHOSE)
    assert asked.headers["Authorization"] == f"Bearer {A_PEER_KEY}"


async def test_a_sandbox_that_does_not_answer_leaves_the_ring_in_production(
    fleet_gateway: Gateway, other_instance: Scripting, caplog: pytest.LogCaptureFixture
) -> None:
    other_instance(get_sandbox_peer, httpx.ReadTimeout("two seconds went by"))

    assert await fleet_gateway.rings_for(AGENT, org=A_RECORD.org, caller=BERNAS_PHONE) is None
    warned = [one.levelname for one in caplog.records if THERE in one.getMessage()]
    assert warned == ["WARNING"]


async def test_a_production_with_no_sandbox_named_answers_every_ring_itself(
    fleet_gateway: Gateway,
) -> None:
    assert await fleet_gateway.rings_for(AGENT, org=A_RECORD.org, caller=BERNAS_PHONE) is None


# The sandbox's side of the numbers: production's own fleet key reads any org's production doors.
async def test_a_key_production_minted_reads_the_production_doors_of_any_org(
    wired: None,  # noqa: ARG001
    keys: MemoryKeys,
    routes: MemoryRoutes,
) -> None:
    door = Route(A_RECORD.org, AGENT, "phone", "+14176743169")
    await routes.put(door)

    async with over_the_asgi_app(f"Bearer {await a_peer_key(keys, PRODUCTION)}") as sandbox:
        said = await sandbox.get("/v1/routes", params={"org": A_RECORD.org, "env": PRODUCTION})

    assert said.json() == [asdict(door)]
