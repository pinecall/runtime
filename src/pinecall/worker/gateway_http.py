"""One hop to the gateway: a request and its answer, a stream and its frames, a refusal."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Mapping
from typing import Any

import httpx

from pinecall.session.voice.platform import PlatformRefused
from pinecall.types.json import JsonObject
from pinecall.types.org import Ceiling

# A call is not worth waiting on a control plane for: the caller is on the line.
TIMEOUT_S = 5.0

# GET /v1/calls/{call}/events is one door with two flavours, and Accept is what picks the stream:
# a page otherwise. The gateway spells the same media type at its sink.
EVENT_STREAM = "text/event-stream"

# A stream on a quiet call carries a comment every 25 s and an entry whenever there is one; the
# read side waits for either as long as the call lasts, and only the connect is on the clock.
# remember() is on the same clock: the session bounds it by its own budget and cancels the wait.
TAIL_TIMEOUT = httpx.Timeout(TIMEOUT_S, read=None)

# What every door that names a call answers for a call the gateway is not serving.
NOT_FOUND = 404


class GatewayRefused(PlatformRefused):
    """The gateway answered anything but yes; the call ends before the caller has spoken."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        # The HTTP status it answered with, or None when it could not be reached at all.
        self.status = status


async def read(
    http: httpx.AsyncClient,
    method: str,
    path: str,
    said: Any = None,
    timeout: float | httpx.Timeout | None = None,
    params: Mapping[str, str] | None = None,
) -> Any:
    """One request, and the body of the answer. Anything but a 2xx is a refusal by name."""
    answer = await _answered(http, method, path, said, timeout, params)
    return answer.json() if answer.content else None


# A clip is a few hundred kilobytes, fetched once per box and then kept by its hash.
FILE_TIMEOUT_S = 15.0


async def fetch_bytes(http: httpx.AsyncClient, path: str, params: Mapping[str, str]) -> bytes:
    """One GET, and the bytes it answered with — a file, not JSON — on the file's own clock."""
    return (await _answered(http, "GET", path, None, FILE_TIMEOUT_S, params)).content


async def _answered(
    http: httpx.AsyncClient,
    method: str,
    path: str,
    said: Any,
    timeout: float | httpx.Timeout | None,
    params: Mapping[str, str] | None,
) -> httpx.Response:
    """The answer itself, once it is a 2xx; a refusal names the door and carries the status."""
    waiting = TIMEOUT_S if timeout is None else timeout
    try:
        answer = await http.request(method, path, json=said, timeout=waiting, params=params or None)
    except httpx.HTTPError as unreachable:
        raise GatewayRefused(f"{method} {path}: {unreachable}") from unreachable
    if answer.status_code >= httpx.codes.BAD_REQUEST:
        refusal = f"{method} {path}: {answer.status_code} {answer.text}"
        raise GatewayRefused(refusal, answer.status_code)
    return answer


# A door whose 404 means "nothing by that name" is asked a yes or a no, not a refusal: a code
# nobody issued is what a caller keying an extension gets, and it ends nothing.
async def found(asking: Awaitable[Any]) -> bool:
    """Whether the gateway said yes: False for a 404, and any other refusal raised as it came."""
    try:
        await asking
    except GatewayRefused as refused:
        if refused.status != NOT_FOUND:
            raise
        return False
    return True


async def stream_json(http: httpx.AsyncClient, path: str) -> AsyncIterator[JsonObject]:
    """One server-sent stream, message by message, for as long as the gateway holds it open."""
    headers = {"Accept": EVENT_STREAM}
    try:
        async with http.stream("GET", path, headers=headers, timeout=TAIL_TIMEOUT) as s:
            if s.status_code >= httpx.codes.BAD_REQUEST:
                raise GatewayRefused(f"GET {path}: {s.status_code}", s.status_code)
            async for frame in server_sent_events(s.aiter_lines()):
                yield frame
    except httpx.HTTPError as unreachable:
        raise GatewayRefused(f"GET {path}: {unreachable}") from unreachable


# The reader the gateway's sink writes for: `id:` is the seq, `event:` the type, and `data:` the
# entry as JSON, which already carries both — so the data line is the whole message and the rest is
# the browser's business. A comment line is the gateway keeping the connection warm.
async def server_sent_events(lines: AsyncIterator[str]) -> AsyncIterator[JsonObject]:
    """Each SSE message's data, decoded, in the order the stream carried them."""
    data: list[str] = []
    async for line in lines:
        line = line.rstrip("\r\n")
        if line.startswith("data:"):
            data.append(line[5:].lstrip(" "))
        elif not line and data:
            yield json.loads("\n".join(data))
            data = []


# A gateway older than the ceiling answered 204, and one whose org's minutes have no limit answers
# null: both are no ceiling at all.
def parse_ceiling(answer: Any) -> Ceiling | None:
    """What POST /v1/calls answered about the org's minutes, as the worker keeps it."""
    if answer is None or answer.get("seconds_left") is None:
        return None
    return Ceiling(seconds=int(answer["seconds_left"]), minutes=int(answer["minutes"]))
