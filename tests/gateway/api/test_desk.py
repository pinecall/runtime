"""Tests for the supervisor's desk."""

import asyncio
import json
import time

import httpx

from pinecall.tenancy import keys, reads, tokens
from pinecall.wire.rest.calls import OpenCallRequest, SealCallRequest
from tests.conftest import (
    AGENT,
    Knocking,
    postgres,
)
from tests.gateway.api.conftest import a_call, first_data


@postgres
async def test_the_orgs_key_lands_the_verb_on_the_workers_queue_naming_the_org(
    knocking: Knocking,
) -> None:
    context = a_call(knocking)
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=OpenCallRequest(agent=AGENT, context=context).written())
        async with knocking.http(knocking.app["sandbox"]) as desk:
            taken = await desk.post(
                f"/v1/calls/{context.call}/verbs", json={"verb": "say", "text": "un momento"}
            )
        async with worker.stream(
            "GET", f"/v1/calls/{context.call}/commands", headers={"Accept": "text/event-stream"}
        ) as stream:
            data = json.loads(await asyncio.wait_for(first_data(stream.aiter_lines()), 5))
    assert taken.status_code == 202
    assert data["type"] == "supervisor.verb"
    assert data["data"]["by"] == {"id": f"key:{knocking.org.id}"}
    assert data["data"]["verb"] == {"verb": "say", "text": "un momento"}


@postgres
async def test_a_supervise_token_sends_the_verb_under_its_own_identity_and_reads_only_its_call(
    knocking: Knocking,
) -> None:
    context = a_call(knocking)
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=OpenCallRequest(agent=AGENT, context=context).written())
        visitor = tokens.Visitor(expires_at=time.time() + 60, subject="mem_ana", name="Ana")
        token = tokens.room_token(knocking.gateway.signer, context.call, "supervise", visitor)
        async with knocking.http(token) as desk:
            taken = await desk.post(f"/v1/calls/{context.call}/verbs", json={"verb": "takeover"})
            elsewhere = await desk.post("/v1/calls/call_other/verbs", json={"verb": "takeover"})
        async with worker.stream(
            "GET", f"/v1/calls/{context.call}/commands", headers={"Accept": "text/event-stream"}
        ) as stream:
            data = json.loads(await asyncio.wait_for(first_data(stream.aiter_lines()), 5))
    assert taken.status_code == 202
    assert elsewhere.status_code == 403
    assert data["data"]["by"] == {"id": "mem_ana", "name": "Ana"}


@postgres
async def test_a_verb_needs_a_bearer_a_running_call_and_one_that_is_not_over(
    knocking: Knocking,
) -> None:
    context = a_call(knocking)
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=OpenCallRequest(agent=AGENT, context=context).written())
        async with httpx.AsyncClient(base_url=knocking.url) as nobody:
            unsigned = await nobody.post(
                f"/v1/calls/{context.call}/verbs", json={"verb": "takeover"}
            )
        async with knocking.http(knocking.app["sandbox"]) as desk:
            nowhere = await desk.post("/v1/calls/call_nobody/verbs", json={"verb": "takeover"})
        await worker.post(
            f"/v1/calls/{context.call}/sealed",
            json=SealCallRequest(usage=[], outcome="over").written(),
        )
        async with knocking.http(knocking.app["sandbox"]) as desk:
            over = await desk.post(f"/v1/calls/{context.call}/verbs", json={"verb": "takeover"})
    assert unsigned.status_code == 401
    assert nowhere.status_code == 404
    assert over.status_code == 409
    assert "read its log" in over.json()["detail"]


@postgres
async def test_a_seat_that_listens_or_supervises_is_on_the_record_as_who_took_it(
    knocking: Knocking,
) -> None:
    context = a_call(knocking)
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=OpenCallRequest(agent=AGENT, context=context).written())
    async with knocking.http(knocking.app["sandbox"]) as desk:
        listening = await desk.post(f"/v1/calls/{context.call}/listen")
        supervising = await desk.post(f"/v1/calls/{context.call}/supervise")
    pool = knocking.gateway.connections.pool
    server = await keys.verify(pool, knocking.app["sandbox"])
    assert server is not None
    rows = await reads.of_org(pool, knocking.org.id, subject=context.call)
    assert (listening.status_code, supervising.status_code) == (200, 200)
    assert sorted((row.what, row.reader, row.env) for row in rows) == [
        ("listen", server.key.key_id, "sandbox"),
        ("supervise", server.key.key_id, "sandbox"),
    ]
