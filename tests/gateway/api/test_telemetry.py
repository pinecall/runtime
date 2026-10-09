"""Tests for the telemetry doors: set, read, dropped, scoped, and handed to the worker."""

from tests.conftest import Knocking, issued, postgres
from tests.gateway.api.test_agents import AGENT, an_app

TELEMETRY = "/v1/telemetry"
A_COLLECTOR = {
    "endpoint": "https://otel.example.test/v1/traces",
    "headers": {"x-api-key": "made-up"},
}


@postgres
async def test_the_org_sets_its_collector_reads_it_back_by_header_name_and_drops_it(
    knocking: Knocking,
) -> None:
    async with knocking.http(knocking.app["sandbox"]) as org:
        nowhere = await org.get(TELEMETRY)
        kept = await org.put(TELEMETRY, json=A_COLLECTOR)
        read = await org.get(TELEMETRY)
        bad = await org.put(TELEMETRY, json={"endpoint": "otel.example.test:4317"})
        dropped = await org.delete(TELEMETRY)
        again = await org.delete(TELEMETRY)
    assert (nowhere.status_code, nowhere.json()) == (200, None)
    assert kept.status_code == 204
    assert read.json() == {
        "endpoint": A_COLLECTOR["endpoint"],
        "header_names": ["x-api-key"],
        "pii": False,
    }
    assert "made-up" not in read.text
    assert bad.status_code == 400
    assert (dropped.status_code, again.status_code) == (204, 404)


@postgres
async def test_a_key_without_the_providers_scope_sets_no_collector(knocking: Knocking) -> None:
    reads = await issued(
        knocking.gateway.connections.pool, knocking.org.id, "sandbox", frozenset({"calls"})
    )
    async with knocking.http(reads) as reader:
        refused = await reader.put(TELEMETRY, json=A_COLLECTOR)
    assert refused.status_code == 403


@postgres
async def test_the_worker_is_handed_the_collector_with_its_headers_open(knocking: Knocking) -> None:
    socket = await an_app(knocking)
    scope = {"org": knocking.org.id, "env": "sandbox", "holder": ""}
    async with knocking.http(knocking.app["sandbox"]) as org:
        await org.put(TELEMETRY, json={**A_COLLECTOR, "pii": True})
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        stages = (await worker.get(f"/v1/agents/{AGENT}/provider-keys", params=scope)).json()
    assert stages["telemetry"] == {
        "endpoint": A_COLLECTOR["endpoint"],
        "headers": {"x-api-key": "made-up"},
        "pii": True,
    }
    await socket.close()
