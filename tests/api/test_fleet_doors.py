"""The fleet's doors over the real app: a heartbeat in, the roster out, a cordon, a number left."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from pinecall.api.agents.registry import Registry
from pinecall.auth.keys import MemoryKeys
from pinecall.fleet import Heartbeat
from pinecall.log.store import MemoryStore
from pinecall.types import DEFAULT_ORG, PRODUCTION, THE_FLEET
from pinecall.worker.client import Gateway, GatewayRefused
from tests.api.conftest import A_KEY, A_RECORD, AGENT, over_the_asgi_app

pytestmark = pytest.mark.unit

OPS_FLEET = "/v1/ops/fleet"
CALLBACKS = "/v1/callbacks"


# The fleet knocks with the key pinecall-worker-key.service mints — org default, the `fleet`
# scope — and that is not the tenant's key the rest of this suite holds, so one is issued here.
@pytest.fixture
async def fleet_gateway(wired: None, keys: MemoryKeys) -> AsyncIterator[Gateway]:  # noqa: ARG001
    """worker/client.py over the real app, knocking with the fleet's own key."""
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
