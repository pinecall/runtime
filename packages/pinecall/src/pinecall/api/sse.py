"""Server-sent events, the one way this gateway streams: the frame, the ping, the end at a stop."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, FastAPI, Header
from starlette.requests import HTTPConnection
from starlette.responses import StreamingResponse

from pinecall.types.json import JsonObject

SSE = "text/event-stream"

# How long a browser waits before reconnecting, in milliseconds. One second: an EventSource that
# reconnects with its Last-Event-ID loses nothing, so there is no reason to make it wait.
RETRY_MS = 1000

# A comment frame every 25 s. It is not a heartbeat the protocol knows about — it is bytes, so that
# a proxy with a 30 s idle timeout does not cut a stream that is simply on a quiet call.
PING_SECONDS = 25.0

# SSE has no body to put a status in, so nginx and friends are told here not to hold onto one.
SSE_HEADERS = {"cache-control": "no-store", "connection": "keep-alive", "x-accel-buffering": "no"}

# What a quiet stream says so the connection is seen to be alive: a comment, which no reader parses.
PING = ": ping\n\n"


# ── the gateway is stopping ─────────────────────────────────────────────────────


# A stream never ends by itself: a floor's feed and a call's tail stay open for as long as the
# reader does. A server stopping waits for its requests to finish, so without a word from the
# process every stream held the stop to its grace period and was then cancelled mid-write —
# `Exception in ASGI application … timeout graceful shutdown exceeded`, once per open console at
# every deploy. The process says "closing" the moment it is told to stop (cli/gateway.py); each
# stream ends there, cleanly, and its reader reconnects to the next process with its Last-Event-ID.
def new_closing(gateway: FastAPI) -> asyncio.Event:
    """The event every stream of this process ends on, made once by the lifespan."""
    closing = asyncio.Event()
    gateway.state.closing = closing
    return closing


def announce_closing(gateway: FastAPI) -> None:
    """Tell every open stream the process is stopping. Safe from a signal handler.

    A signal lands between two bytecodes of whatever the loop was running, so the event is set
    through the loop's own thread-safe queue rather than here. Before the lifespan made the event
    (a stop during the start) there is no stream to tell.
    """
    closing: asyncio.Event | None = getattr(gateway.state, "closing", None)
    if closing is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        closing.set()
        return
    loop.call_soon_threadsafe(closing.set)


def get_closing(connection: HTTPConnection) -> asyncio.Event:
    """The process's closing event, for a door that streams."""
    closing: asyncio.Event = connection.app.state.closing
    return closing


ClosingDep = Annotated[asyncio.Event, Depends(get_closing)]
AcceptDep = Annotated[str | None, Header()]


# ── the stream ──────────────────────────────────────────────────────────────────


def wants_sse(accept: str | None) -> bool:
    """Whether this reader asked for the stream. One URL, two flavours, and Accept decides."""
    return SSE in (accept or "")


def sse_stream(frames: AsyncIterator[str], closing: asyncio.Event) -> StreamingResponse:
    """Frames as an SSE response that ends when they do, or when the process starts to stop."""
    return StreamingResponse(until(closing, frames), media_type=SSE, headers=SSE_HEADERS)


# The one place a frame is spelled (the log's, the usage rows', the app's commands'): the id when
# the reader can resume from it, the event, the data on one line, the blank line that ends it.
def sse_frame(event: str, data: JsonObject, *, id: int | None = None) -> str:
    """One SSE frame, compact JSON as the data line."""
    said = json.dumps(data, separators=(",", ":"))
    head = "" if id is None else f"id: {id}\n"
    return f"{head}event: {event}\ndata: {said}\n\n"


# The stream and the clock are two sources and SSE needs both. One pending task carried across the
# timeout is the whole trick: re-awaiting a fresh __anext__ after a ping would drop the entry the
# first one is still waiting for.
async def pace[T](coming: AsyncIterator[T], every: float) -> AsyncIterator[T | None]:
    """Every item as it comes, and None whenever `every` seconds pass with nothing to send."""
    pending: asyncio.Task[T] | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(anext(coming))
            done, _ = await asyncio.wait({pending}, timeout=every)
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


# The same trick against a second source: the item being waited for is cancelled, never dropped
# into a frame nobody reads, the moment the process says it is stopping.
async def until[T](closing: asyncio.Event, coming: AsyncIterator[T]) -> AsyncIterator[T]:
    """Every item as it comes, until there are no more or `closing` is set."""
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
