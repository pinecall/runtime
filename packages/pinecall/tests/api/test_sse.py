"""SSE, as every stream of this gateway writes it: the frame, the ping, and the end at a stop."""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import cast

import pytest
from fastapi import FastAPI

from pinecall.api.sse import (
    SSE,
    SSE_HEADERS,
    announce_closing,
    new_closing,
    pace,
    sse_frame,
    sse_stream,
    until,
    wants_sse,
)

pytestmark = pytest.mark.unit


async def never() -> AsyncIterator[str]:
    """A source with nothing to say, ever: a quiet call, a floor where nothing moves."""
    await asyncio.Event().wait()
    # Unreachable; the yield is what makes this an async generator and not a coroutine.
    yield "never said"


async def said(*frames: str) -> AsyncIterator[str]:
    for frame in frames:
        yield frame


def test_a_frame_is_its_id_its_event_and_one_line_of_json() -> None:
    frame = sse_frame("user.said", {"text": "hola"}, id=7)
    assert frame == 'id: 7\nevent: user.said\ndata: {"text":"hola"}\n\n'
    assert json.loads(sse_frame("usage", {"a": 1}).split("data: ")[1]) == {"a": 1}


def test_accept_decides_between_the_page_and_the_stream() -> None:
    assert wants_sse(SSE)
    assert wants_sse(f"application/json, {SSE}")
    assert not wants_sse("application/json")
    assert not wants_sse(None)


async def test_a_quiet_stream_is_kept_open_by_a_ping() -> None:
    """The pacing is the whole ping: a source that never speaks yields None every `every`."""
    ticks = pace(never(), every=0.01)
    assert await anext(ticks) is None
    assert await anext(ticks) is None


async def test_a_stream_says_everything_its_source_does_and_then_ends() -> None:
    assert [frame async for frame in until(asyncio.Event(), said("a", "b"))] == ["a", "b"]


# The bug this exists for: a floor's feed never ends, so a stopping gateway waited out its grace
# period on it and then cancelled it mid-write — an ASGI exception at every deploy.
async def test_a_stream_waiting_on_a_quiet_source_ends_the_moment_the_process_stops() -> None:
    closing = asyncio.Event()
    frames = until(closing, never())
    reading = asyncio.ensure_future(anext(frames, None))
    await asyncio.sleep(0)
    closing.set()
    assert await asyncio.wait_for(reading, timeout=1) is None


async def test_the_response_is_sse_and_carries_the_headers_a_proxy_must_not_buffer() -> None:
    response = sse_stream(said("x"), asyncio.Event())
    assert response.media_type == SSE
    for name, value in SSE_HEADERS.items():
        assert response.headers[name] == value
    body = cast("AsyncIterator[str]", response.body_iterator)
    assert [frame async for frame in body] == ["x"]


async def test_the_process_announces_its_stop_through_the_loop() -> None:
    gateway = FastAPI()
    closing = new_closing(gateway)
    announce_closing(gateway)
    await asyncio.wait_for(closing.wait(), timeout=1)


def test_a_stop_before_the_lifespan_made_the_event_has_nobody_to_tell() -> None:
    announce_closing(FastAPI())
