"""The worker's only door to the platform: the gateway, over HTTP. No database, no cache."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from typing import Any

import httpx
from pydantic import TypeAdapter

from pinecall.fleet import Heartbeat, Standing
from pinecall.session.voice.platform import PlatformRefused
from pinecall.types import (
    AgentConfig,
    CallContext,
    Channel,
    Env,
    PlatformTool,
    ProviderKeys,
    Route,
)
from pinecall.types.json import JsonObject
from pinecall_protocol import Command
from pinecall_protocol.defs import ToolResult
from pinecall_protocol.events import ToolCall

# A call is not worth waiting on a control plane for: the caller is on the line.
TIMEOUT_S = 5.0

# GET /v1/calls/{call}/events is one door with two flavours, and Accept is what picks the stream:
# a page otherwise. The gateway spells the same media type at its sink.
EVENT_STREAM = "text/event-stream"

# A stream on a quiet call carries a comment every 25 s and an entry whenever there is one; the
# read side waits for either as long as the call lasts, and only the connect is on the clock.
# remember() is on the same clock: the session bounds it by its own budget and cancels the wait.
TAIL_TIMEOUT = httpx.Timeout(TIMEOUT_S, read=None)

# The hop carries the domain object itself, adapted by pydantic. The one wire-to-domain conversion
# in the tree is providers/declaration.py, at the app's edge, and this is deliberately not a
# second one: two processes of the same distribution exchange the class they both already hold.
ROUTES: TypeAdapter[tuple[Route, ...]] = TypeAdapter(tuple[Route, ...])
CONFIG: TypeAdapter[AgentConfig] = TypeAdapter(AgentConfig)
KEYS: TypeAdapter[dict[str, str]] = TypeAdapter(dict[str, str])
CONTEXT: TypeAdapter[CallContext] = TypeAdapter(CallContext)
RESULT: TypeAdapter[ToolResult] = TypeAdapter(ToolResult)
COMMAND: TypeAdapter[Command] = TypeAdapter(Command)
BEAT: TypeAdapter[Heartbeat] = TypeAdapter(Heartbeat)
STANDING: TypeAdapter[Standing] = TypeAdapter(Standing)


class GatewayRefused(PlatformRefused):
    """The gateway answered anything but yes; the call ends before the caller has spoken."""


class Gateway:
    """Everything the worker knows it asked here: the routes, an agent's config, the call's log."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    # The three doors that name WHOSE: the worker holds one key for every org, so each is asked
    # with the corner the dispatch named — the org, the world, the holder — and the gateway answers
    # that org's. Nothing named is the key's own corner, which is what a laptop worker on a
    # tenant's key still gets. A phone call on the box's own trunk names no org: the number and
    # the channel are asked instead, and the gateway finds the one door across every org.
    async def routes(
        self,
        *,
        org: str | None = None,
        env: Env | None = None,
        holder: str | None = None,
        number: str | None = None,
        channel: Channel | None = None,
    ) -> tuple[Route, ...]:
        """The doors of one corner, or the one door a number rings, as the domain holds them."""
        asked = _whose(org, env, holder)
        if number is not None:
            asked["number"] = number
        if channel is not None:
            asked["channel"] = channel
        return ROUTES.validate_python(await self._read("GET", "/v1/routes", params=asked))

    async def agent(
        self,
        slug: str,
        *,
        org: str | None = None,
        env: Env | None = None,
        holder: str | None = None,
    ) -> AgentConfig:
        """What the app declared about this agent, resolved: the session is built from it."""
        said = await self._read("GET", f"/v1/agents/{slug}/config", params=_whose(org, env, holder))
        return CONFIG.validate_python(said)

    # The one answer in the runtime that carries a provider key, and it comes back only to the
    # worker holding this org's own API key — or the fleet's, asking for the org the call is for.
    # Empty for every org that brought none of its own, which is what a managed install is.
    # docs/decisions/provider-keys.md.
    async def provider_keys(
        self,
        slug: str,
        *,
        org: str | None = None,
        env: Env | None = None,
        holder: str | None = None,
    ) -> ProviderKeys:
        """The keys of the org this agent belongs to, for the pipeline this call is built with."""
        said = await self._read(
            "GET", f"/v1/agents/{slug}/provider-keys", params=_whose(org, env, holder)
        )
        return KEYS.validate_python(said["keys"])

    async def opened(self, context: CallContext, agent: str, app: str | None = None) -> None:
        """A call started: the gateway opens its log and every reader of it is subscribed."""
        said: JsonObject = {"agent": agent, "context": CONTEXT.dump_python(context, mode="json")}
        # Which app socket serves this call, when the process that started the worker named one:
        # `pinecall talk` does, so a @tool breakpoint lands in the terminal it was typed in.
        if app is not None:
            said["app"] = app
        await self._read("POST", "/v1/calls", said)

    async def append(
        self, call: str, type: str, data: Mapping[str, Any], ephemeral: bool | None = None
    ) -> None:
        """One entry of this call, with the seq the gateway stamps: the log is written there."""
        said = {"type": type, "data": dict(data), "ephemeral": ephemeral}
        await self._read("POST", f"/v1/calls/{call}/events", said)

    async def tool(self, call: str, agent: str, wanted: ToolCall, timeout_s: float) -> ToolResult:
        """One tool out to the app's own process and its result back, through the gateway."""
        said = wanted.model_dump(mode="json", exclude_none=True)
        path = f"/v1/calls/{call}/tools?agent={agent}"
        # The tool's own deadline plus the hop, never the control plane's five seconds: the
        # gateway answers a slow app with a lapsed result at exactly timeout_s, and a client that
        # gave up first would turn that sentence the model can read into a dead connection.
        answer = await self._read("POST", path, said, timeout=timeout_s + TIMEOUT_S)
        return RESULT.validate_python(answer)

    async def sealed(self, call: str) -> None:
        """The call is over and nothing more will be written to it."""
        await self._read("POST", f"/v1/calls/{call}/sealed")

    # ── the fleet's two doors ───────────────────────────────────────────────────

    async def heartbeat(self, beat: Heartbeat) -> Standing:
        """What this worker holds, to the hub; back comes whether it was cordoned."""
        said = await self._read("POST", "/v1/fleet/heartbeat", BEAT.dump_python(beat, mode="json"))
        return STANDING.validate_python(said)

    async def fleet_is_full(self) -> bool:
        """Whether no worker of the fleet can take a call right now: the overflow's one question."""
        said = await self._read("GET", "/v1/fleet/standing")
        return bool(said["full"])

    async def callback_requested(
        self, agent: str, channel: Channel, number: str, call: str | None
    ) -> None:
        """The number the overflow agent took, onto the agent's log, for the app to dial back."""
        said: JsonObject = {
            "agent": agent,
            "channel": channel,
            "number": number,
            "via": "overflow",
            "call": call,
        }
        await self._read("POST", "/v1/callbacks", said)

    # The two doors memory and retrieval sit behind, on the gateway that has the database: the
    # worker holds no vectors and no facts, and asks with the caller's words. The gateway writes
    # memory.ops and docs.sources on the call's log itself. This is the voice session's Lookup and
    # Rememberer, as it is its Platform: the same object, three protocols. docs/decisions/memory.md.
    async def lookup(
        self, call: str, tool: PlatformTool, input: Mapping[str, Any], speech_id: str | None
    ) -> Mapping[str, Any]:
        """One run of recall or search on the gateway: the JSON object its tool result carries."""
        said: JsonObject = {"tool": tool, "input": dict(input)}
        if speech_id is not None:
            said["speech_id"] = speech_id
        answer = await self._read("POST", f"/v1/calls/{call}/lookup", said)
        return dict(answer["output"])

    async def remember(self, call: str) -> int:
        """The gateway reads the call's turns off its log and writes what memory keeps."""
        said = await self._read("POST", f"/v1/calls/{call}/remember", {}, timeout=TAIL_TIMEOUT)
        return int(said["ops"])

    # The worker writes the log and never learns a seq: the gateway numbers it. What a browser in
    # the room is sent must carry the seq, so the worker reads its own call back through the same
    # three doors the console reads — whole, because its key is the runtime's, and projected by the
    # room before a byte leaves for the browser.
    async def state(self, call: str) -> tuple[JsonObject, int]:
        """The call's reduced state and the seq it was folded to: what a widget starts from."""
        said = await self._read("GET", f"/v1/calls/{call}/state")
        return said["state"], int(said["last_seq"])

    async def since(self, call: str, after: int) -> AsyncIterator[JsonObject]:
        """Every entry above the cursor the store still has, in seq order, page by page."""
        cursor = after
        while True:
            page = await self._read("GET", f"/v1/calls/{call}/events?after={cursor}")
            # 204 is the gateway saying the call is over and the cursor is at its end.
            if page is None or page["next"] is None:
                return
            for entry in page["entries"]:
                yield entry
            cursor = int(page["next"])

    async def tail(self, call: str, after: int) -> AsyncIterator[JsonObject]:
        """The same entries as they are written, then live, until the call ends: the SSE flavour."""
        async for frame in self._streamed(f"/v1/calls/{call}/events?after={after}"):
            yield frame

    # The other direction of the tool round trip: what the app said about this call while it runs.
    # The gateway holds the app's socket, so it is the side that can hand these over, and it hands
    # them over on the same SSE the log is read on. See docs/decisions/voice-bridge.md.
    async def commands(self, call: str) -> AsyncIterator[Command]:
        """Every command the app sent for this call, in order, until the call is sealed."""
        async for frame in self._streamed(f"/v1/calls/{call}/commands"):
            yield COMMAND.validate_python(frame)

    async def _streamed(self, path: str) -> AsyncIterator[JsonObject]:
        """One server-sent stream, message by message, for as long as the gateway holds it open."""
        headers = {"Accept": EVENT_STREAM}
        try:
            async with self._http.stream("GET", path, headers=headers, timeout=TAIL_TIMEOUT) as s:
                if s.status_code >= httpx.codes.BAD_REQUEST:
                    raise GatewayRefused(f"GET {path}: {s.status_code}")
                async for frame in server_sent_events(s.aiter_lines()):
                    yield frame
        except httpx.HTTPError as unreachable:
            raise GatewayRefused(f"GET {path}: {unreachable}") from unreachable

    async def aclose(self) -> None:
        """Close the connection pool the process opened once."""
        await self._http.aclose()

    async def _read(
        self,
        method: str,
        path: str,
        said: Any = None,
        timeout: float | httpx.Timeout | None = None,
        params: Mapping[str, str] | None = None,
    ) -> Any:
        """One request, and the body of the answer. Anything but a 2xx is a refusal by name."""
        waiting = TIMEOUT_S if timeout is None else timeout
        try:
            answer = await self._http.request(
                method, path, json=said, timeout=waiting, params=params or None
            )
        except httpx.HTTPError as unreachable:
            raise GatewayRefused(f"{method} {path}: {unreachable}") from unreachable
        if answer.status_code >= httpx.codes.BAD_REQUEST:
            raise GatewayRefused(f"{method} {path}: {answer.status_code} {answer.text}")
        return answer.json() if answer.content else None


def _whose(org: str | None, env: Env | None, holder: str | None) -> dict[str, str]:
    """The corner as the doors take it on the query string: only the coordinates that were named."""
    return {
        name: value
        for name, value in (("org", org), ("env", env), ("holder", holder))
        if value is not None
    }


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


def reaching(base_url: str, key: str = "") -> Gateway:
    """The gateway at that URL (Settings.gateway_url), with the worker's key on every request."""
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    return Gateway(httpx.AsyncClient(base_url=base_url, headers=headers, timeout=TIMEOUT_S))
