"""The org's two doors: its calls across every agent, and the stream of its floor changing."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterable
from typing import cast

import pytest
from starlette.testclient import TestClient

from pinecall.api.agents.registry import Registry
from pinecall.api.calls.live_calls import events
from pinecall.api.calls.log_sink import a_projection
from pinecall.log.store import MemoryStore
from pinecall.log.writers import ORG_EVENTS, Logs
from pinecall.types import PRODUCTION
from tests.api.calls.test_listing import OVER, RINGING, UP
from tests.api.conftest import A_READER, A_RECORD, AGENT
from tests.api.talking import got

pytestmark = pytest.mark.unit

ANOTHER_AGENT = "tienda-sur"
THE_SHOP_NEXT_DOOR = "vecina"


async def a_call(
    store: MemoryStore, call: str, agent: str, org: str, *, ended: bool = False
) -> None:
    """One call as a worker writes it, owned by the org whose key opened it."""
    await store.owned(call, agent, org)
    await store.append(call, agent, "call.ringing", dict(RINGING))
    await store.append(call, agent, "call.started", dict(UP))
    if ended:
        await store.append(call, agent, "call.ended", dict(OVER))


async def test_the_orgs_sessions_span_its_agents_newest_first_and_stop_at_its_fence(
    gateway: TestClient, store: MemoryStore
) -> None:
    await a_call(store, "CA_first", AGENT, A_RECORD.org, ended=True)
    await a_call(store, "CA_second", ANOTHER_AGENT, A_RECORD.org)
    await a_call(store, "CA_theirs", "clinica-vecina", THE_SHOP_NEXT_DOOR)
    status, body = got(gateway, "/v1/sessions")
    assert status == 200
    assert [(line["call"], line["status"]) for line in body["calls"]] == [
        ("CA_second", "active"),
        ("CA_first", "ended"),
    ]
    _, one = got(gateway, "/v1/sessions?limit=1")
    assert [line["call"] for line in one["calls"]] == ["CA_second"]


def test_a_room_token_reads_its_one_call_and_never_an_orgs_list(gateway: TestClient) -> None:
    from pinecall.auth.scopes import mint_room_token
    from tests.api.conftest import A_LIVEKIT

    token = mint_room_token("CA_first", "participate", 4102444800.0, A_LIVEKIT)
    status, body = got(gateway, "/v1/sessions", bearer=token)
    assert status == 403
    assert body["detail"] == "an org's events are read with a key"


def test_the_feed_carries_the_floor_and_nothing_said_on_a_call() -> None:
    """The closed set: an agent held, a call arriving, up, over, waiting on a person and one on
    the line. A turn is the call's alone."""
    assert {
        "agent.registered",
        "agent.detached",
        "call.ringing",
        "call.dialing",
        "call.started",
        "call.ended",
        "attention.requested",
        "attention.answered",
        "supervisor.took_over",
        "supervisor.released",
    } == ORG_EVENTS


async def test_a_register_and_a_call_reach_the_orgs_feed_and_a_turn_does_not(
    logs: Logs, registry: Registry
) -> None:
    feed = logs.feed(A_RECORD.org).subscribe()
    await registry.register("app_1", A_RECORD.org, PRODUCTION, AGENT)
    await logs.owned("CA_1", AGENT, A_RECORD.org)
    log = logs.writing("CA_1", AGENT)
    await log.append("call.ringing", dict(RINGING))
    await log.append("turn.user", {"text": "hola", "speech_id": "u1"})
    await log.append("call.started", dict(UP))
    heard = [await asyncio.wait_for(feed.__anext__(), 1) for _ in range(3)]
    assert [entry.type for entry in heard] == ["agent.registered", "call.ringing", "call.started"]
    assert [entry.call for entry in heard] == [None, "CA_1", "CA_1"]
    feed.close()


async def test_another_orgs_call_never_reaches_this_orgs_feed(logs: Logs) -> None:
    feed = logs.feed(A_RECORD.org).subscribe()
    await logs.owned("CA_theirs", "clinica-vecina", THE_SHOP_NEXT_DOOR)
    await logs.writing("CA_theirs", "clinica-vecina").append("call.ringing", dict(RINGING))
    await logs.owned("CA_ours", AGENT, A_RECORD.org)
    await logs.writing("CA_ours", AGENT).append("call.ringing", dict(RINGING))
    heard = await asyncio.wait_for(feed.__anext__(), 1)
    assert heard.call == "CA_ours"
    feed.close()


async def test_the_events_door_streams_the_feed_as_sse_from_now_on(
    logs: Logs, registry: Registry
) -> None:
    """A register while the stream is open lands as one SSE frame carrying the entry."""
    # The door's own body, read as the browser would read it: httpx's ASGI transport hands a
    # response back whole, and a stream that never ends never comes back through it.
    answer = await events(A_READER, logs, a_projection(registry))
    # starlette types the body as either flavour of iterable; this door streams text, always.
    chunks = aiter(cast("AsyncIterable[str]", answer.body_iterator))
    assert (await anext(chunks)).startswith("retry:")
    await registry.register("app_1", A_RECORD.org, PRODUCTION, AGENT)
    frame = await asyncio.wait_for(anext(chunks), 2)
    lines = frame.strip().splitlines()
    assert lines[1] == "event: agent.registered"
    said = json.loads(lines[2][len("data: ") :])
    assert (said["type"], said["agent"], said["data"]["env"]) == (
        "agent.registered",
        AGENT,
        PRODUCTION,
    )
