"""The worker's only door to the platform: the gateway, over HTTP. No database, no cache."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from functools import partial
from typing import Any, cast

import httpx
from pydantic import ValidationError

from pinecall.fleet import Heartbeat, RingsFor, Standing
from pinecall.session.voice.platform import Dialled
from pinecall.types import (
    AgentConfig,
    Brought,
    CallContext,
    Channel,
    Env,
    PlatformTool,
    Route,
)
from pinecall.types.dispatch import Handover
from pinecall.types.json import JsonObject
from pinecall.types.org import Ceiling
from pinecall.types.refused_answer import refusal_detail
from pinecall.worker.gateway_http import (
    NOT_FOUND,
    TAIL_TIMEOUT,
    TIMEOUT_S,
    GatewayRefused,
    fetch_bytes,
    found,
    parse_ceiling,
    read,
    stream_json,
)
from pinecall.worker.retries import again
from pinecall.worker.wire import (
    BEAT,
    COMMAND,
    CONFIG,
    CONTEXT,
    JUDGING,
    KEYS,
    LENDS,
    RESULT,
    ROUTES,
    STANDING,
    HoldAudioSaid,
)
from pinecall_protocol import Command
from pinecall_protocol.defs import ToolResult
from pinecall_protocol.events import ToolCall

# A seal is asked again while the gateway is away, well inside the job's own SEALING_S: a call the
# worker could not seal is sealed by the gateway's reaper once its room is gone
# (api/calls/reaper.py).
SEALED_WITHIN_S = 30.0


class Gateway:
    """Everything the worker knows it asked here: the routes, an agent's config, the call's log."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http
        # What each call was opened with, until it is sealed: a gateway that restarted forgot the
        # call, and this is what it is told again (POST /v1/calls/{call}/reopened).
        self._opened: dict[str, JsonObject] = {}

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
    # Empty keys for every org that brought none of its own, which is what a managed install is;
    # `lends` is what the box lends it beside them, absent from a gateway older than it (all lent).
    # docs/decisions/provider-keys.md.
    async def provider_keys(
        self,
        slug: str,
        *,
        org: str | None = None,
        env: Env | None = None,
        holder: str | None = None,
    ) -> Brought:
        """What the org this agent belongs to brought and is lent, for this call's pipeline."""
        said = await self._read(
            "GET", f"/v1/agents/{slug}/provider-keys", params=_whose(org, env, holder)
        )
        lends = said.get("lends")
        return Brought(
            keys=KEYS.validate_python(said["keys"]),
            lends=None if lends is None else frozenset(LENDS.validate_python(lends)),
        )

    async def hold_audio(
        self,
        slug: str,
        *,
        org: str | None = None,
        env: Env | None = None,
        holder: str | None = None,
    ) -> HoldAudioSaid:
        """Which melody this agent plays while a tool runs: default, off, or a clip by its hash."""
        said = await self._read(
            "GET", f"/v1/agents/{slug}/hold-audio", params=_whose(org, env, holder)
        )
        return HoldAudioSaid.model_validate(said)

    async def hold_audio_file(
        self,
        slug: str,
        *,
        org: str | None = None,
        env: Env | None = None,
        holder: str | None = None,
    ) -> bytes:
        """The clip itself, Ogg Opus as the gateway converted it."""
        path = f"/v1/agents/{slug}/hold-audio/audio"
        return await fetch_bytes(self._http, path, _whose(org, env, holder))

    # The gateway judges the number against the org's guards before it answers with a trunk, so a
    # refusal here is "not this number, not this often" and not "no trunk" — the two read very
    # differently in a caller's log, and the verb writes whichever happened.
    async def outbound_trunk(
        self,
        slug: str,
        *,
        org: str | None = None,
        env: Env | None = None,
        holder: str | None = None,
        to: str,
        call: str,
    ) -> Dialled:
        """The trunk this leg dials out through once the number passed the org's guards."""
        asked = {**_whose(org, env, holder), "to": to, "call": call}
        try:
            said = await self._read("GET", f"/v1/agents/{slug}/outbound-trunk", params=asked)
        except GatewayRefused as refused:
            return Dialled(refused=refusal_detail(str(refused)))
        trunk = cast("dict[str, object]", said).get("trunk") if isinstance(said, dict) else None
        return Dialled(trunk=trunk if isinstance(trunk, str) and trunk else None)

    async def rings_for(self, slug: str, *, org: str, caller: str) -> Handover | None:
        """The corner and the fleet this production ring is handed to, or None: production's."""
        said = await self._read(
            "GET", f"/v1/agents/{slug}/rings-for", params={"org": org, "caller": caller}
        )
        return RingsFor.model_validate(said).handover()

    async def opened(
        self, context: CallContext, agent: str, app: str | None = None
    ) -> Ceiling | None:
        """A call started: its log opened, and what the org's minutes leave it, or None."""
        said: JsonObject = {"agent": agent, "context": CONTEXT.dump_python(context, mode="json")}
        # The app socket `pinecall talk` named, so a @tool breakpoint lands in its terminal.
        if app is not None:
            said["app"] = app
        answer = await self._read("POST", "/v1/calls", said)
        self._opened[context.call] = said
        return parse_ceiling(answer)

    async def append(
        self, call: str, type: str, data: Mapping[str, Any], ephemeral: bool | None = None
    ) -> None:
        """One entry of this call, with the seq the gateway stamps: the log is written there."""
        said = {"type": type, "data": dict(data), "ephemeral": ephemeral}
        path = f"/v1/calls/{call}/events"
        # Asked again for as long as the gateway is away: an entry dropped is a hole in the log.
        await again(lambda: self._on_the_call(call, "POST", path, said), within_s=None, what=path)

    async def tool(self, call: str, agent: str, wanted: ToolCall, timeout_s: float) -> ToolResult:
        """One tool out to the app's own process and its result back, through the gateway."""
        said = wanted.model_dump(mode="json", exclude_none=True)
        path = f"/v1/calls/{call}/tools?agent={agent}"
        # The tool's own deadline plus the hop, never the control plane's five seconds: the
        # gateway answers a slow app with a lapsed result at exactly timeout_s, and a client that
        # gave up first would turn that sentence the model can read into a dead connection.
        waiting = timeout_s + TIMEOUT_S
        # A tool is one round trip per call_id at the gateway, so asking again writes nothing twice;
        # it is asked again only within its own deadline, which the model is waiting on.
        answer = await again(
            lambda: self._on_the_call(call, "POST", path, said, timeout=waiting),
            within_s=waiting,
            what=path,
        )
        return RESULT.validate_python(answer)

    async def sealed(self, call: str) -> None:
        """The call is over and nothing more will be written to it."""
        path = f"/v1/calls/{call}/sealed"
        await again(
            lambda: self._on_the_call(call, "POST", path), within_s=SEALED_WITHIN_S, what=path
        )
        self._opened.pop(call, None)

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
        answer = await self._on_the_call(call, "POST", f"/v1/calls/{call}/lookup", said)
        return dict(answer["output"])

    # Asked at hang-up, before a judge may spend on a model. A gateway that cannot be asked is a
    # call judged as every call was before the setting existed: the ceiling still bounds it.
    async def judging(self, call: str) -> bool:
        """Whether the org this call belongs to judges its calls at hang-up."""
        try:
            asked = await self._on_the_call(call, "GET", f"/v1/calls/{call}/judging")
            said = JUDGING.validate_python(asked)
        except (GatewayRefused, ValidationError):
            return True
        return said.on

    async def remember(self, call: str) -> int:
        """The gateway reads the call's turns off its log and writes what memory keeps."""
        path = f"/v1/calls/{call}/remember"
        said = await self._on_the_call(call, "POST", path, {}, timeout=TAIL_TIMEOUT)
        return int(said["ops"])

    async def claim(self, call: str, code: str) -> bool:
        """Bind this call to the page showing that code; False when nobody issued it."""
        path = f"/v1/calls/{call}/claim"
        return await found(self._on_the_call(call, "POST", path, {"code": code}))

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
            path = f"/v1/calls/{call}/events?after={cursor}"
            # Asked again while the gateway is away, inside the seal's own patience: a hang-up
            # reads its verdict from this, and a gateway restarting at that moment used to be a
            # GatewayRefused out of the shutdown callback (2026-09-26).
            page = await again(
                partial(self._read, "GET", path), within_s=SEALED_WITHIN_S, what=path
            )
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
        path = f"/v1/calls/{call}/commands"
        try:
            async for frame in self._streamed(path):
                yield COMMAND.validate_python(frame)
            return
        except GatewayRefused as refused:
            if not await self._reopened(call, refused):
                raise
        async for frame in self._streamed(path):
            yield COMMAND.validate_python(frame)

    async def _read(
        self,
        method: str,
        path: str,
        said: Any = None,
        timeout: float | httpx.Timeout | None = None,
        params: Mapping[str, str] | None = None,
    ) -> Any:
        """One request on this worker's connection pool."""
        return await read(self._http, method, path, said, timeout, params)

    def _streamed(self, path: str) -> AsyncIterator[JsonObject]:
        """One server-sent stream on this worker's connection pool."""
        return stream_json(self._http, path)

    async def aclose(self) -> None:
        """Close the connection pool the process opened once."""
        await self._http.aclose()

    # Every door that names a call answers 404 for a call the gateway is not serving. When this
    # worker opened that call, the gateway is the one that forgot it — it restarted — so it is told
    # again what the call is, once, and the request is asked again.
    async def _on_the_call(
        self,
        call: str,
        method: str,
        path: str,
        said: Any = None,
        timeout: float | httpx.Timeout | None = None,
    ) -> Any:
        """One request about a call this worker holds, reopened once if the gateway forgot it."""
        try:
            return await self._read(method, path, said, timeout)
        except GatewayRefused as refused:
            if not await self._reopened(call, refused):
                raise
        return await self._read(method, path, said, timeout)

    async def _reopened(self, call: str, refused: GatewayRefused) -> bool:
        """Whether that refusal was a gateway that forgot this call, now told it again."""
        opened = self._opened.get(call)
        if refused.status != NOT_FOUND or opened is None:
            return False
        await self._read("POST", f"/v1/calls/{call}/reopened", opened)
        return True


def _whose(org: str | None, env: Env | None, holder: str | None) -> dict[str, str]:
    """The corner as the doors take it on the query string: only the coordinates that were named."""
    return {
        name: value
        for name, value in (("org", org), ("env", env), ("holder", holder))
        if value is not None
    }


def build_gateway(base_url: str, key: str = "") -> Gateway:
    """The gateway at that URL (Settings.gateway_url), with the worker's key on every request."""
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    return Gateway(httpx.AsyncClient(base_url=base_url, headers=headers, timeout=TIMEOUT_S))
