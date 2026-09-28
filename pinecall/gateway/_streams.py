"""Server-sent events: the frames, the pings that keep a stream alive, its end on shutdown."""

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi.responses import StreamingResponse

from pinecall.domain.names import JsonObject

SSE = "text/event-stream"

# Short: a reconnect resumes losslessly from Last-Event-ID.
RETRY_MS = 1000

# Under the 30 s a proxy lets a quiet connection idle.
PING_S = 25.0

PING = ": ping\n\n"

# x-accel-buffering: nginx would hold the stream in its buffer.
SSE_HEADERS = {"cache-control": "no-store", "connection": "keep-alive", "x-accel-buffering": "no"}


def wants_sse(accept: str | None) -> bool:
    """Whether the Accept header asks for the stream rather than a page."""
    return SSE in (accept or "")


def frame(event: str, data: JsonObject, *, seq: int | None = None) -> str:
    """One SSE frame: its id (the seq), its event, its data as one line of JSON."""
    head = "" if seq is None else f"id: {seq}\n"
    return f"{head}event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def streamed(frames: AsyncIterator[str], closing: asyncio.Event) -> StreamingResponse:
    """Frames as an SSE response, ended when they run out or the process stops."""
    return StreamingResponse(until(closing, frames), media_type=SSE, headers=SSE_HEADERS)


# One pending __anext__ across pings: a new one after a ping would lose the item the first waits
# for.
async def paced[T](coming: AsyncIterator[T], every_s: float = PING_S) -> AsyncIterator[T | None]:
    """The items as they come, and None after every quiet stretch."""
    pending: asyncio.Task[T] | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(anext(coming))
            done, _ = await asyncio.wait({pending}, timeout=every_s)
            if not done:
                yield None
                continue
            finished, pending = pending, None
            try:
                yield finished.result()
            except StopAsyncIteration:
                return
    finally:
        if pending is not None:
            pending.cancel()


async def until[T](closing: asyncio.Event, coming: AsyncIterator[T]) -> AsyncIterator[T]:
    """The items until they end or the process starts closing."""
    stopping = asyncio.ensure_future(closing.wait())
    pending: asyncio.Task[T] | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(anext(coming))
            await asyncio.wait({pending, stopping}, return_when=asyncio.FIRST_COMPLETED)
            if not pending.done():
                return
            finished, pending = pending, None
            try:
                yield finished.result()
            except StopAsyncIteration:
                return
    finally:
        stopping.cancel()
        if pending is not None:
            pending.cancel()
