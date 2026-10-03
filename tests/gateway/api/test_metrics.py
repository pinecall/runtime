"""Tests for GET /metrics: what the append doors count, what the gateway holds, on loopback."""

import time

import httpx

from pinecall.domain.names import JsonObject
from pinecall.gateway.api.metrics import from_any
from pinecall.wire.rest.calls import OpenCallRequest
from tests.conftest import AGENT, Knocking, postgres
from tests.gateway.api.conftest import a_call

FAILED = (
    "type='stt_error' timestamp=1.0 label='livekit.plugins.deepgram.stt.STT' "
    "error=APIStatusError('bad key', status_code=401) recoverable=False"
)


A_BEAT: JsonObject = {
    "fleet": "pinecall-sandbox",
    "active": 0,
    "max_jobs": 4,
    "load": 0.1,
    "draining": False,
}


def line_of(text: str, starting: str) -> str:
    """The one line of the exposition that starts so."""
    found = [line for line in text.splitlines() if line.startswith(starting)]
    assert len(found) == 1, found
    return found[0]


@postgres
async def test_the_appends_are_timed_counted_and_a_vendors_failures_named(
    knocking: Knocking,
) -> None:
    context = a_call(knocking)
    failure: JsonObject = {"code": "component_failed", "message": FAILED, "recoverable": False}
    batch: JsonObject = {
        "after": 0,
        "entries": [
            {"type": "custom", "data": {"name": "first", "data": {}}, "ts": 0.1},
            {"type": "error", "data": failure, "ts": 0.2},
        ],
    }
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=OpenCallRequest(agent=AGENT, context=context).written())
        await worker.post(f"/v1/calls/{context.call}/entries", json=batch)
        await worker.post(
            f"/v1/calls/{context.call}/events",
            json={"type": "error", "data": {**failure, "code": "tool_skipped", "message": "x"}},
        )
    async with httpx.AsyncClient(base_url=knocking.url) as scraper:
        read = await scraper.get("/metrics")
    assert read.status_code == 200
    assert read.headers["content-type"].startswith("text/plain; version=0.0.4")
    assert "pinecall_writer_waiting 0.0" in read.text
    text = read.text
    assert (
        line_of(text, "pinecall_entries_appended_total ") == "pinecall_entries_appended_total 3.0"
    )
    assert line_of(text, "pinecall_append_seconds_count ") == "pinecall_append_seconds_count 2"
    assert line_of(text, 'pinecall_append_seconds_bucket{le="+Inf"}').endswith(" 2")
    vendor = 'pinecall_errors_total{code="component_failed",vendor="deepgram"}'
    assert line_of(text, vendor) == f"{vendor} 1.0"
    assert line_of(text, 'pinecall_errors_total{code="tool_skipped",vendor=""}').endswith(" 1.0")
    assert (
        line_of(text, 'pinecall_held{what="calls_live"}') == 'pinecall_held{what="calls_live"} 1.0'
    )
    in_use = float(line_of(text, 'pinecall_pool_connections{state="in_use"}').split()[-1])
    most = float(line_of(text, 'pinecall_pool_connections{state="max"}').split()[-1])
    assert 0 <= in_use <= most
    assert "# TYPE pinecall_append_seconds histogram" in text


@postgres
async def test_a_failing_vendor_and_each_replicas_lag_are_families_of_their_own(
    knocking: Knocking,
) -> None:
    counted = knocking.gateway.counters
    for n in range(5):
        counted.handed_out(["deepgram"], time.time())
        counted.failed_on("deepgram", f"call_{n}", time.time())
    async with httpx.AsyncClient(base_url=knocking.url) as scraper:
        read = (await scraper.get("/metrics")).text
    assert 'pinecall_vendor_failing{vendor="deepgram"} 1.0' in read
    assert "# TYPE pinecall_replication_lag_seconds gauge" in read


@postgres
async def test_an_orgs_unusual_spend_stands_on_metrics_while_it_lasts(knocking: Knocking) -> None:
    knocking.gateway.counters.spending("org_a", 3.5)
    async with httpx.AsyncClient(base_url=knocking.url) as scraper:
        flagged = (await scraper.get("/metrics")).text
        knocking.gateway.counters.spending("org_a", None)
        usual = (await scraper.get("/metrics")).text
    assert 'pinecall_spend_unusual{org="org_a"} 3.5' in flagged
    assert "pinecall_spend_unusual{" not in usual


@postgres
async def test_a_request_that_came_through_the_proxy_is_refused(knocking: Knocking) -> None:
    async with httpx.AsyncClient(base_url=knocking.url) as outside:
        forwarded = await outside.get("/metrics", headers={"x-forwarded-for": "203.0.113.9"})
        from_the_box = await outside.get("/metrics", headers={"x-forwarded-for": "127.0.0.1"})
    assert (forwarded.status_code, from_the_box.status_code) == (403, 403)
    assert "never a forwarded request" in forwarded.json()["detail"]


# A cluster's Prometheus scrapes from the pods' network, which the setting names; anyone else is
# refused, and a forwarded request whatever its address.
def test_metrics_answer_the_networks_named_and_nothing_else() -> None:
    pods = "127.0.0.1,::1,10.111.0.0/16"
    assert from_any("10.111.4.7", pods)
    assert from_any("::1", pods)
    assert not from_any("10.112.0.3", pods)
    assert not from_any("unknown", pods)


# A failing minute beside a sound one: the sound one takes the calls the other is not counted for.
@postgres
async def test_each_worker_says_how_the_roster_counts_it_and_its_first_audio(
    knocking: Knocking,
) -> None:
    bad: JsonObject = {**A_BEAT, "worker": "w1", "ended": 4, "failed": 3, "errors": 5}
    good: JsonObject = {**A_BEAT, "worker": "w2", "turns": 12, "first_audio_p95_s": 0.9}
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        assert (await worker.post("/v1/fleet/heartbeat", json=bad)).is_success
        assert (await worker.post("/v1/fleet/heartbeat", json=good)).is_success
    async with httpx.AsyncClient(base_url=knocking.url) as scraper:
        text = (await scraper.get("/metrics")).text
    state = 'pinecall_worker_state{fleet="pinecall-sandbox",worker="w1",state="failing"}'
    assert line_of(text, state) == f"{state} 1.0"
    state = 'pinecall_worker_state{fleet="pinecall-sandbox",worker="w2",state="accepting"}'
    assert line_of(text, state) == f"{state} 1.0"
    waited = 'pinecall_worker_first_audio_p95_seconds{fleet="pinecall-sandbox",worker="w2"}'
    assert line_of(text, waited) == f"{waited} 0.9"
    assert 'first_audio_p95_seconds{fleet="pinecall-sandbox",worker="w1"}' not in text
    accepting = 'pinecall_fleet{fleet="pinecall-sandbox",what="accepting"}'
    assert line_of(text, accepting) == f"{accepting} 1.0"
