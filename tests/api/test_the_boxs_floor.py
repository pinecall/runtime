"""GET /v1/ops/events: every org's floor on one stream, each frame naming its org."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterable, AsyncIterator
from typing import cast

import httpx
import pytest

from pinecall.api.ops_floor import events
from pinecall.log.writers import Logs
from pinecall.types.json import JsonObject
from pinecall_protocol.rest import BoxEvent
from tests.api.calls.test_listing import RINGING

pytestmark = pytest.mark.unit

ASKED: JsonObject = {"reason": "quiere hablar con una persona", "wait_s": 60}


async def frames(logs: Logs) -> AsyncIterator[str]:
    """The door's own body, read as a notifier reads it: it never ends, so never over HTTP."""
    answer = await events(logs)
    chunks = aiter(cast("AsyncIterable[str]", answer.body_iterator))
    assert (await anext(chunks)).startswith("retry:")
    return chunks


def said(frame: str) -> tuple[str, JsonObject]:
    """A frame's event name and its data."""
    lines = frame.strip().splitlines()
    return lines[1][len("event: ") :], json.loads(lines[2][len("data: ") :])


async def test_two_orgs_calls_arrive_on_one_stream_each_with_its_org(logs: Logs) -> None:
    chunks = await frames(logs)
    await logs.owned("CA_a", "clinica-norte", "org_a")
    await logs.owned("CA_b", "tienda-sur", "org_b")
    await logs.writing("CA_a", "clinica-norte").append("call.ringing", dict(RINGING))
    await logs.writing("CA_b", "tienda-sur").append("call.ringing", dict(RINGING))
    heard = [said(await asyncio.wait_for(anext(chunks), 2)) for _ in range(2)]
    assert [(event, data["org"], data["entry"]["call"]) for event, data in heard] == [
        ("call.ringing", "org_a", "CA_a"),
        ("call.ringing", "org_b", "CA_b"),
    ]
    for _, data in heard:
        BoxEvent.model_validate(data)


async def test_a_call_asking_for_a_person_reaches_the_box(logs: Logs) -> None:
    chunks = await frames(logs)
    await logs.owned("CA_a", "clinica-norte", "org_a")
    await logs.writing("CA_a", "clinica-norte").append("attention.requested", dict(ASKED))
    event, data = said(await asyncio.wait_for(anext(chunks), 2))
    assert (event, data["org"], data["entry"]["data"]["reason"]) == (
        "attention.requested",
        "org_a",
        ASKED["reason"],
    )


async def test_the_box_floor_is_the_operators_alone(stranger: httpx.AsyncClient) -> None:
    assert (await stranger.get("/v1/ops/events")).status_code == 401
    stranger.headers["Authorization"] = "Bearer pc_nobody_at_all_on_this_box_000"
    assert (await stranger.get("/v1/ops/events")).status_code == 401
