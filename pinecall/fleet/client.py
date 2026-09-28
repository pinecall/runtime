"""The worker's one door to the platform: the gateway over HTTP, on its fleet's key."""

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Iterator, Mapping

import httpx
from pydantic import TypeAdapter

from pinecall.domain.agent import AgentConfig
from pinecall.domain.call import Route
from pinecall.domain.errors import GatewayRefused
from pinecall.domain.names import Channel, Json, JsonObject
from pinecall.domain.scope import Scope
from pinecall.wire.frames import Command, Entry
from pinecall.wire.parts import PlatformTool, ToolResult
from pinecall.wire.rest.agents import HoldAudio, RingHandoff
from pinecall.wire.rest.calls import (
    CallbackRequest,
    LogPage,
    OpenCallRequest,
    OpenCallResponse,
    SealCallRequest,
)
from pinecall.wire.rest.fleet import FleetTotals, HeartbeatRequest, HeartbeatResponse
from pinecall.wire.rest.numbers import LegTrunk, LegTrunkResponse

logger = logging.getLogger(__name__)


# A caller is waiting on the line.
TIMEOUT_S = 5.0


# A clip is a few hundred KB, fetched once per box.
A_FILE_S = 15.0


# Under the job's sealing budget (worker/main.py); what does not seal here the reaper seals.
SEALED_WITHIN_S = 30.0


# A restarting gateway is back in seconds; the cap keeps the log catching up after it.
FIRST_WAIT_S = 0.5


LONGEST_WAIT_S = 5.0


SAID_EVERY = 10


NOT_SERVED = 404


EVENT_STREAM = "text/event-stream"


_ROUTES: TypeAdapter[list[Route]] = TypeAdapter(list[Route])


_CONFIG: TypeAdapter[AgentConfig] = TypeAdapter(AgentConfig)


_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


class GatewayClient:
    """The gateway as a worker asks it: every door it knocks, on one connection pool."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        """No call opened yet."""
        self.http = http
        # What each unsealed call was opened with, said again to a gateway that restarted.
        self.opened: dict[str, OpenCallRequest] = {}

    # ── an agent, before its call ──

    async def routes(
        self, scope: Scope | None, *, number: str | None, channel: Channel
    ) -> list[Route]:
        """The routes of the dispatch's scope; with no scope, the one a number rings, any org."""
        params = _scope_headers(scope)
        if number is not None:
            params |= {"number": number, "channel": channel}
        return _ROUTES.validate_python(await self._read("GET", "/v1/routes", params=params))

    async def agent(self, slug: str, scope: Scope) -> AgentConfig:
        """The agent as the scope runs it: its declaration under the scope's settings."""
        text = await self._read("GET", f"/v1/agents/{slug}/config", params=_scope_headers(scope))
        return _CONFIG.validate_python(text)

    # The one answer that carries keys, and it carries them to the fleet's key alone.
    async def stages(self, slug: str, scope: Scope) -> Json:
        """The three stages the call runs, each with the key it runs on."""
        return await self._read(
            "GET", f"/v1/agents/{slug}/provider-keys", params=_scope_headers(scope)
        )

    async def hold_audio(self, slug: str, scope: Scope) -> HoldAudio:
        """What the agent plays while a tool runs."""
        text = await self._read(
            "GET", f"/v1/agents/{slug}/hold-audio", params=_scope_headers(scope)
        )
        return HoldAudio.model_validate(text)

    async def hold_clip(self, slug: str, scope: Scope) -> bytes:
        """The org's own clip, Ogg Opus."""
        path = f"/v1/agents/{slug}/hold-audio/audio"
        answer = await self._answered("GET", path, None, A_FILE_S, _scope_headers(scope))
        return answer.content

    async def rings_for(self, slug: str, org: str, caller: str) -> RingHandoff:
        """Whether a production ring from this phone belongs in a developer's sandbox."""
        params = {"org": org, "caller": caller}
        text = await self._read("GET", f"/v1/agents/{slug}/rings-for", params=params)
        return RingHandoff.model_validate(text)

    # The gateway judges the leg (shape and pace) and names the trunk; a refusal is its sentence.
    async def leg(
        self, slug: str, scope: Scope, *, to: str, call: str, shown: str | None
    ) -> LegTrunk:
        """How to dial a leg of the call: the trunk inline and the number shown."""
        params = (
            _scope_headers(scope) | {"to": to, "call": call} | ({"from": shown} if shown else {})
        )
        text = await self._read("GET", f"/v1/agents/{slug}/outbound-trunk", params=params)
        return LegTrunkResponse.model_validate(text).trunk

    # ── a call ──

    async def open(self, opening: OpenCallRequest) -> OpenCallResponse:
        """Open the call's log; the answer is what the org's minutes leave it."""
        data = await self._read("POST", "/v1/calls", opening.written())
        self.opened[opening.context.call] = opening
        return OpenCallResponse.model_validate(data)

    # Retried without a limit: an entry dropped is a hole in the log.
    async def append(
        self, call: str, kind: str, data: JsonObject, *, ephemeral: bool | None = None
    ) -> Entry:
        """Write one entry; the gateway numbers it."""
        payload: JsonObject = {"type": kind, "data": data, "ephemeral": ephemeral}
        path = f"/v1/calls/{call}/events"
        answer = await again(lambda: self._on_the_call(call, "POST", path, payload), None, path)
        return Entry.model_validate(answer)

    # The gateway answers a lapsed tool at its own deadline: wait past it, so the model reads
    # that answer and not a timeout of ours. Idempotent per call id, so a retry joins.
    async def tool(self, call: str, agent: str, called: JsonObject, timeout_s: float) -> ToolResult:
        """Run a tool in the app that declared it and return what it answered."""
        waiting = timeout_s + TIMEOUT_S
        path = f"/v1/calls/{call}/tools?agent={agent}"
        answer = await again(
            lambda: self._on_the_call(call, "POST", path, called, waiting), waiting, path
        )
        return ToolResult.model_validate(answer)

    async def sealed(self, call: str, sealing: SealCallRequest) -> None:
        """Hand the end of the call to the gateway, which prices, judges and seals it."""
        path = f"/v1/calls/{call}/sealed"
        body = sealing.written()
        await again(lambda: self._on_the_call(call, "POST", path, body), SEALED_WITHIN_S, path)
        self.opened.pop(call, None)

    async def lookup(
        self, call: str, tool: PlatformTool, arguments: Mapping[str, Json], speech: str | None
    ) -> JsonObject:
        """Recall or search, run by the gateway, which writes what it found on the log."""
        data: JsonObject = {"tool": tool, "input": dict(arguments), "speech_id": speech}
        answer = _OBJECT.validate_python(
            await self._on_the_call(call, "POST", f"/v1/calls/{call}/lookup", data)
        )
        return _OBJECT.validate_python(answer.get("output") or {})

    # A code nobody issued is an ordinary answer: the caller may be dialling an extension.
    async def claim(self, call: str, code: str) -> bool:
        """Tie the call to the page showing the code; False when no page waits on it."""
        try:
            await self._on_the_call(call, "POST", f"/v1/calls/{call}/claim", {"code": code})
        except GatewayRefused as refused:
            if refused.answered != NOT_SERVED:
                raise
            return False
        return True

    async def state(self, call: str) -> JsonObject:
        """The call's state as the gateway folds it, with the seq it was folded to."""
        return _OBJECT.validate_python(
            await self._on_the_call(call, "GET", f"/v1/calls/{call}/state")
        )

    async def since(self, call: str, after: int) -> AsyncIterator[Entry]:
        """The stored entries above the cursor, page by page, until the log's end."""
        cursor = after
        while True:
            path = f"/v1/calls/{call}/events?after={cursor}"
            page = await again(lambda p=path: self._read("GET", p), SEALED_WITHIN_S, path)
            if page is None:
                return
            read = LogPage.model_validate(page)
            for entry in read.entries:
                yield Entry.model_validate(entry)
            if read.next is None:
                return
            cursor = read.next

    async def tail(self, call: str, after: int) -> AsyncIterator[Entry]:
        """The entries above the cursor, then the live ones, until the call ends."""
        async for frame in self._streamed(f"/v1/calls/{call}/events?after={after}"):
            yield Entry.model_validate(frame)

    # A stream that ends means the gateway went away; a 404 means it forgot the call.
    async def commands(self, call: str) -> AsyncGenerator[Command]:
        """The app's commands for the call, in the order it sent them, until the call is sealed."""
        path = f"/v1/calls/{call}/commands"
        try:
            async for frame in self._streamed(path):
                yield Command.model_validate(frame)
        except GatewayRefused as refused:
            if not await self._reopened(call, refused):
                raise
        else:
            return
        async for frame in self._streamed(path):
            yield Command.model_validate(frame)

    # ── the fleet ──

    async def heartbeat(self, beat: HeartbeatRequest) -> HeartbeatResponse:
        """Report this worker; the answer says whether it is cordoned."""
        return HeartbeatResponse.model_validate(
            await self._read("POST", "/v1/fleet/heartbeat", beat.written())
        )

    async def fleet_is_full(self, fleet: str) -> bool:
        """Whether no worker of the fleet can take a call."""
        text = await self._read("GET", "/v1/fleet/standing", params={"fleet": fleet})
        return FleetTotals.model_validate(text).full

    async def callback(self, wanted: CallbackRequest) -> None:
        """Somebody the overflow told to wait for a call back."""
        await self._read("POST", "/v1/callbacks", wanted.written())

    async def aclose(self) -> None:
        """Close the connection pool."""
        await self.http.aclose()

    # ── the wire ──

    async def _read(
        self,
        method: str,
        path: str,
        data: Json = None,
        wait_s: float | None = None,
        params: Mapping[str, str] | None = None,
    ) -> Json:
        answer = await self._answered(method, path, data, wait_s, params)
        return answer.json() if answer.content else None

    async def _answered(
        self,
        method: str,
        path: str,
        data: Json,
        wait_s: float | None,
        params: Mapping[str, str] | None,
    ) -> httpx.Response:
        try:
            answer = await self.http.request(
                method, path, json=data, timeout=wait_s or TIMEOUT_S, params=params or None
            )
        except httpx.HTTPError as unreachable:
            raise GatewayRefused(f"{method} {path}: {unreachable}") from unreachable
        if answer.status_code >= httpx.codes.BAD_REQUEST:
            raise GatewayRefused(
                f"{method} {path}: {answer.status_code} {answer.text}", answered=answer.status_code
            )
        return answer

    # A 404 for a call this worker opened is a gateway that restarted: say the call again, once.
    async def _on_the_call(
        self, call: str, method: str, path: str, data: Json = None, wait_s: float | None = None
    ) -> Json:
        try:
            return await self._read(method, path, data, wait_s)
        except GatewayRefused as refused:
            if not await self._reopened(call, refused):
                raise
        return await self._read(method, path, data, wait_s)

    async def _reopened(self, call: str, refused: GatewayRefused) -> bool:
        opening = self.opened.get(call)
        if refused.answered != NOT_SERVED or opening is None:
            return False
        await self._read("POST", f"/v1/calls/{call}/reopened", opening.written())
        return True

    async def _streamed(self, path: str) -> AsyncIterator[JsonObject]:
        try:
            async with self.http.stream(
                "GET", path, headers={"Accept": EVENT_STREAM}, timeout=STREAMED
            ) as stream:
                if stream.status_code >= httpx.codes.BAD_REQUEST:
                    raise GatewayRefused(
                        f"GET {path}: {stream.status_code}", answered=stream.status_code
                    )
                async for frame in server_sent(stream.aiter_lines()):
                    yield frame
        except httpx.HTTPError as unreachable:
            raise GatewayRefused(f"GET {path}: {unreachable}") from unreachable


# A stream is timed only on connect: a quiet one is kept open by the gateway's ping every 25 s.
STREAMED = httpx.Timeout(TIMEOUT_S, read=None)


def gateway_at(url: str, key: str | None) -> GatewayClient:
    """The gateway at this URL, knocked on with the fleet's key in the header, never the URL."""
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    return GatewayClient(httpx.AsyncClient(base_url=url, headers=headers, timeout=TIMEOUT_S))


# `data:` holds the whole entry, its seq and type included: `id:`, `event:` and comments are
# not read.
async def server_sent(lines: AsyncIterator[str]) -> AsyncIterator[JsonObject]:
    """Each JSON message of a server-sent event stream."""
    data: list[str] = []
    async for raw in lines:
        line = raw.rstrip("\r\n")
        if line.startswith("data:"):
            data.append(line[5:].lstrip(" "))
        elif not line and data:
            text = json.loads("\n".join(data))
            data = []
            if isinstance(text, dict):
                yield text


def waits() -> Iterator[float]:
    """The waits between tries: doubling from half a second to five."""
    wait = FIRST_WAIT_S
    while True:
        yield wait
        wait = min(wait * 2, LONGEST_WAIT_S)


def away(refused: GatewayRefused) -> bool:
    """Whether the gateway is away (unreachable or a 5xx), which a retry outlasts."""
    return refused.answered is None or refused.answered >= httpx.codes.INTERNAL_SERVER_ERROR


# A 4xx is an answer. `within_s` bounds a request that has a deadline (a tool); None retries for
# as long as the call lasts (an entry).
async def again[T](attempt: Callable[[], Awaitable[T]], within_s: float | None, what: str) -> T:
    """Run the attempt, retrying while the gateway is away and there is time left."""
    started = time.monotonic()
    pauses = waits()
    tries = 0
    while True:
        tries += 1
        try:
            return await attempt()
        except GatewayRefused as refused:
            pause = next(pauses)
            late = within_s is not None and time.monotonic() - started + pause > within_s
            if not away(refused) or late:
                raise
            if tries % SAID_EVERY == 0:
                logger.warning("%s: the gateway is still away after %d tries", what, tries)
            await asyncio.sleep(pause)


def _scope_headers(scope: Scope | None) -> dict[str, str]:
    if scope is None:
        return {}
    return {"org": scope.org, "env": scope.env, "holder": scope.holder}
