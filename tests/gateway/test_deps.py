"""Tests for what a request is: the bearer, its world and corner, a reader, a stream."""

import asyncio
from collections.abc import AsyncIterator

from pinecall.domain.types import Corner
from pinecall.gateway.deps import (
    SCOPES_OF,
    Reader,
    bearer_of,
    close_reason,
    dispatched,
    frame,
    opening,
    paced,
    until,
    wants_sse,
)
from pinecall.tenancy.keys import Visit


def test_the_bearer_is_read_from_the_header_and_nothing_else_is_one() -> None:
    assert bearer_of({"authorization": "Bearer pc_test_1"}) == "pc_test_1"
    assert bearer_of({"authorization": "Basic abc"}) is None
    assert bearer_of({}) is None


def test_each_scope_a_door_opens_is_recorded_for_the_walk() -> None:
    opened = opening("app", "fleet")
    assert SCOPES_OF[opened] == frozenset({"app", "fleet"})


def test_only_a_dispatch_that_names_org_and_world_is_a_corner() -> None:
    assert dispatched("org_a", "sandbox", "m_1") == Corner("org_a", "sandbox", "m_1")
    assert dispatched("org_a", None, None) is None


def test_a_key_reads_the_tenants_and_a_token_its_grants() -> None:
    assert Reader().projection == "tenant"
    visit = Visit(call="call_1", scope="talk", expires_at=0.0)
    assert Reader(visit=visit).projection == "public"
    read = Visit(call="call_1", scope="read", expires_at=0.0, projection="tenant")
    assert Reader(visit=read).projection == "tenant"


def test_a_close_reason_is_cut_to_what_a_frame_carries_and_never_mid_character() -> None:
    cut = close_reason("ñ" * 100)
    assert len(cut.encode("utf-8")) <= 123
    assert set(cut) == {"ñ"}


def test_a_frame_is_its_id_its_event_and_one_line_of_json() -> None:
    assert frame("custom", {"a": 1}, seq=7) == 'id: 7\nevent: custom\ndata: {"a":1}\n\n'


def test_accept_decides_between_the_page_and_the_stream() -> None:
    assert wants_sse("text/event-stream")
    assert not wants_sse("application/json")
    assert not wants_sse(None)


async def test_a_quiet_stream_is_kept_open_by_a_ping() -> None:
    async def slow() -> AsyncIterator[int]:
        await asyncio.sleep(0.05)
        yield 1

    assert [one async for one in paced(slow(), 0.01)][-1] == 1
    assert None in [one async for one in paced(slow(), 0.01)]


async def test_a_stream_waiting_on_a_quiet_source_ends_the_moment_the_process_stops() -> None:
    closing = asyncio.Event()

    async def never() -> AsyncIterator[int]:
        await asyncio.Event().wait()
        yield 1

    ended = asyncio.create_task(anext(until(closing, never()), None))
    closing.set()
    assert await asyncio.wait_for(ended, 1) is None
