"""WS /v1/apps: the app's side of the protocol — commands in, the agent's own log entries out."""

from __future__ import annotations

import time
from typing import Any, cast

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from pinecall.api._deps import AdmissionDep, KeysDep, LogsDep, a_key_on_a_socket
from pinecall.api.agents.handlers import HANDLERS, Live, LiveDep, Socket, asked, handles
from pinecall.api.agents.registry import Registry, RegistryDep, SocketId, a_socket_id
from pinecall.auth.bearer import POLICY_VIOLATION, as_a_close_reason
from pinecall.auth.keys import KeyRecord, held_by, not_opening
from pinecall.log import REFUSED
from pinecall.log.entry import Entry, unstored
from pinecall.log.writers import Logs
from pinecall.orgs.admission import Admission, QuotaExhausted
from pinecall.types import DeclarationRefused, Env
from pinecall_protocol import Command, ProtocolError, WireModel, encode
from pinecall_protocol.commands import AgentConfigure, AgentRegister
from pinecall_protocol.events import ErrorEvent, Pong
from pinecall_protocol.registry import COMMANDS

router = APIRouter()

# The close code and the header parser are auth's, so that the three doors of this gateway refuse
# a caller the same way and read a credential with the same function.


@router.websocket("/v1/apps")
async def apps(
    websocket: WebSocket,
    keys: KeysDep,
    logs: LogsDep,
    registry: RegistryDep,
    live: LiveDep,
    admission: AdmissionDep,
) -> None:
    """One app, one socket: a key at the door, then commands in and log entries out."""
    key = await a_key_on_a_socket(websocket, keys)
    if key is None:
        await websocket.close(code=POLICY_VIOLATION)
        return
    await websocket.accept()
    # Holding an agent is the `app` scope: a person's key without it is told so and closed.
    if (closed := not_opening(key, "app")) is not None:
        await websocket.close(code=POLICY_VIOLATION, reason=as_a_close_reason(closed))
        return
    socket = AppSocket(websocket, key, logs, registry, live, admission)
    live.connect(socket.id, socket.send)
    try:
        await socket.serve()
    except WebSocketDisconnect:
        pass
    finally:
        live.disconnect(socket.id)
        await registry.release(socket.id)


class AppSocket:
    """One connected app: the key it came in with, and every command it sends over its life."""

    def __init__(
        self,
        websocket: WebSocket,
        key: KeyRecord,
        logs: Logs,
        registry: Registry,
        live: Live,
        admission: Admission,
    ):
        self._websocket = websocket
        self._id = a_socket_id()
        self.key = key
        self.logs = logs
        self.registry = registry
        self.live = live
        self.admission = admission

    @property
    def id(self) -> SocketId:
        """This connection, told apart from every other: what agent.registered hands back."""
        return self._id

    @property
    def org(self) -> str:
        """The org the key names: whose every agent on this socket is."""
        return self.key.org

    @property
    def env(self) -> Env:
        """The world the key opens: where every agent on this socket is held."""
        return self.key.env

    @property
    def holder(self) -> str | None:
        """Whose corner of that world: a developer's own in development, nobody's in production."""
        return held_by(self.key)

    async def serve(self) -> None:
        """Read frames until the app goes away. Every frame is answered, none of them raises out."""
        while True:
            try:
                raw: Any = await self._websocket.receive_json()
            except ValueError as broken:
                await self.refuse("", "bad_shape", f"the frame is not JSON: {broken}", None)
                continue
            await self.take(raw)

    # ── one frame ───────────────────────────────────────────────────────────────

    async def take(self, raw: Any) -> None:
        """One frame: parse it, find its handler, and turn any refusal into an `error` event."""
        try:
            command = _a_command(raw)
        except ProtocolError as refusal:
            await self.refuse(_named(raw, "agent"), "bad_shape", str(refusal), raw)
            return
        handler = HANDLERS.get(command.type)
        if handler is None:
            await self._nobody_to_take(command)
            return
        try:
            await handler(self, command)
        except ProtocolError as refusal:
            await self.refuse(command.agent, "bad_shape", str(refusal), raw)
        except DeclarationRefused as refusal:
            await self.refuse(command.agent, REFUSED, str(refusal), raw)
        # credits.exhausted is already in the agent's log; the app hears the sentence too.
        except QuotaExhausted as refusal:
            await self.refuse(command.agent, REFUSED, str(refusal), raw)

    # Through the live log, so a reader holding this agent's SSE stream open hears the entry now.
    async def emit(self, agent: str, type: str, event: WireModel) -> Entry:
        """Append the event to the agent's own log, then send the entry the store handed back."""
        entry = await self.logs.writing_agent(agent).append(type, encode(event))
        await self.send(entry)
        return entry

    async def send(self, entry: Entry) -> None:
        """One log entry down the wire, exactly as the store keeps it."""
        await self._websocket.send_json(encode(entry))

    async def refuse(self, agent: str, code: str, message: str, raw: Any) -> None:
        """Say no in the protocol's own words, naming the command and the id the app gave it."""
        said: dict[str, Any] = {"code": code, "message": message, "recoverable": True}
        for field, key in (("command", "type"), ("id", "id")):
            if named := _named(raw, key):
                said[field] = named
        error = ErrorEvent(**said)
        if agent:
            await self.emit(agent, "error", error)
            return
        # A frame that named no agent belongs to no log: the app still hears why, unnumbered.
        await self.send(unstored("error", error))

    # A command the protocol knows but this socket cannot run is a call-scoped one: it needs a
    # session, and sessions arrive with the text session card.
    async def _nobody_to_take(self, command: Command) -> None:
        """An unknown type, or a known one that only a live session could have answered."""
        if command.type in COMMANDS:
            await self.refuse(
                command.agent,
                "no_session",
                f"{command.type} belongs to a call and this socket has no session",
                command.model_dump(),
            )
            return
        await self.refuse(
            command.agent,
            "unknown_command",
            f"the protocol has no command called {command.type!r}",
            command.model_dump(),
        )


# ── what this socket answers itself ─────────────────────────────────────────────
#
# The table and its decorator live in apps/handlers.py, with the call-scoped ones in text/.


@handles("agent.register")
async def register(socket: Socket, command: Command) -> None:
    """This socket speaks for this agent and answers these doors, or it is told why not."""
    wanted = asked(command, AgentRegister)
    # One more agent for this org, unless it already holds this one: a socket correcting its own
    # doors, or a second process of the same agent, is not a new agent — and neither is the same
    # slug held in the other world or in another developer's corner. The count is the ORG's slugs
    # across every world and every corner, because the quota is the org's and not a person's.
    others = socket.registry.slugs(socket.org) - {command.agent}
    await socket.admission.an_agent(socket.org, command.agent, len(others))
    entry = await socket.registry.register(
        owner=socket.id,
        org=socket.org,
        env=socket.env,
        holder=socket.holder,
        slug=command.agent,
        routes=wanted.routes,
        sdk=wanted.sdk,
        takes_unclaimed=wanted.takes_unclaimed,
    )
    await socket.send(entry)


@handles("agent.configure")
async def configure(socket: Socket, command: Command) -> None:
    """Declare or change what the agent is. Only the fields the app sent change."""
    wanted = asked(command, AgentConfigure)
    entry = await socket.registry.configure(socket.id, socket.env, command.agent, wanted.config)
    await socket.send(entry)


@handles("ping")
async def ping(socket: Socket, command: Command) -> None:
    """The socket is alive, and the answer says when the gateway thought so."""
    await socket.emit(command.agent, "pong", Pong(ts=time.time()))


# ── the door, and the frame ─────────────────────────────────────────────────────


def _a_command(raw: Any) -> Command:
    """The frame as the protocol's command envelope. Anything else is a ProtocolError."""
    if not isinstance(raw, dict):
        raise ProtocolError("a command is a JSON object")
    try:
        return Command.model_validate(raw)
    except ValidationError as error:
        raise ProtocolError(f"command: {error}") from error


def _named(raw: Any, key: str) -> str:
    """One string off a frame that may not be a frame at all, for an error message."""
    if not isinstance(raw, dict):
        return ""
    named: Any = cast("dict[str, Any]", raw).get(key)
    return named if isinstance(named, str) else ""
