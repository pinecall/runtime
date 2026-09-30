"""Tests for GET /metrics: what the append doors count, what the gateway holds, on loopback."""

import httpx

from pinecall.domain.names import JsonObject
from pinecall.wire.rest.calls import OpenCallRequest
from tests.conftest import AGENT, Knocking, postgres
from tests.gateway.api.conftest import a_call

FAILED = (
    "type='stt_error' timestamp=1.0 label='livekit.plugins.deepgram.stt.STT' "
    "error=APIStatusError('bad key', status_code=401) recoverable=False"
)


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
async def test_a_request_that_came_through_the_proxy_is_refused(knocking: Knocking) -> None:
    async with httpx.AsyncClient(base_url=knocking.url) as outside:
        forwarded = await outside.get("/metrics", headers={"x-forwarded-for": "203.0.113.9"})
        from_the_box = await outside.get("/metrics", headers={"x-forwarded-for": "127.0.0.1"})
    assert (forwarded.status_code, from_the_box.status_code) == (403, 403)
    assert "loopback alone" in forwarded.json()["detail"]
