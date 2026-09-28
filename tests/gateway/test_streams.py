"""Tests for server-sent events: frames, pings, and the end of a stream."""

import asyncio
from collections.abc import AsyncIterator

from pinecall.gateway._deps import close_reason
from pinecall.gateway._streams import frame, paced, until, wants_sse


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

    assert [item async for item in paced(slow(), 0.01)][-1] == 1
    assert None in [item async for item in paced(slow(), 0.01)]


async def test_a_stream_waiting_on_a_quiet_source_ends_the_moment_the_process_stops() -> None:
    closing = asyncio.Event()

    async def never() -> AsyncIterator[int]:
        await asyncio.Event().wait()
        yield 1

    ended = asyncio.create_task(anext(until(closing, never()), None))
    closing.set()
    assert await asyncio.wait_for(ended, 1) is None
