"""WS /v1/apps: the app's side of the protocol — commands in, the agent's own log entries out."""

from __future__ import annotations

import time
from typing import Any, cast

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from pinecall.api._deps import (
    AdmissionDep,
    KeysDep,
    KnowledgeDep,
    LogsDep,
    MembersDep,
    SettingsDep,
    TuningDep,
    a_key_on_a_socket,
)
from pinecall.api.agents.handlers import HANDLERS, Live, LiveDep, Socket, asked, handles
from pinecall.api.agents.holding import SocketId, a_socket_id
from pinecall.api.agents.processes import Process, Processes, ProcessesDep
from pinecall.api.agents.registry import Registry, RegistryDep
from pinecall.api.agents.tuned import tuned_for
from pinecall.api.calls.attaching import handed_on, parked_calls_of
from pinecall.auth.bearer import POLICY_VIOLATION, as_a_close_reason
from pinecall.auth.corner import author_of
from pinecall.auth.keys import KeyRecord, held_by, not_opening
from pinecall.knowledge import Knowledge
from pinecall.log import REFUSED
from pinecall.log.entry import Entry, unstored
from pinecall.log.writers import Logs
from pinecall.orgs.admission import Admission, QuotaExhausted
from pinecall.orgs.tuning import TuningStore
from pinecall.providers import declaration
from pinecall.types import AgentConfig, DeclarationRefused, Env
from pinecall_protocol import Command, ProtocolError, WireModel, encode
from pinecall_protocol.commands import AgentConfigure, AgentRegister
from pinecall_protocol.events import ErrorEvent, Pong
from pinecall_protocol.registry import COMMANDS

router = APIRouter()

# The error code an app hears when a member of its org stopped it (POST /v1/apps/{app}/stop): the
# protocol's error.json names it, and the SDK exits on it instead of reconnecting.
STOPPED = "stopped"

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
    tuning: TuningDep,
    knowledge: KnowledgeDep,
    members: MembersDep,
    processes: ProcessesDep,
    settings: SettingsDep,
) -> None:
    """One app, one socket: a key at the door, then commands in and log entries out."""
    try:
        key = await a_key_on_a_socket(websocket, keys, members, settings.sandbox_domain)
    except PermissionError as refused:
        await websocket.accept()
        await websocket.close(code=POLICY_VIOLATION, reason=as_a_close_reason(str(refused)))
        return
    if key is None:
        await websocket.close(code=POLICY_VIOLATION)
        return
    await websocket.accept()
    await keys.touch(key.key_id)
    # Holding an agent is the `app` scope: a person's key without it is told so and closed.
    if (closed := not_opening(key, "app")) is not None:
        await websocket.close(code=POLICY_VIOLATION, reason=as_a_close_reason(closed))
        return
    socket = AppSocket(
        websocket, key, logs, registry, live, admission, tuning, knowledge, processes
    )
    live.connect(socket.id, socket.send)
    client = websocket.client
    processes.opened(
        Process(
            app=socket.id,
            org=key.org,
            env=key.env,
            holder=held_by(key),
            address=None if client is None else client.host,
            connected_at=time.time(),
            stop=socket.stopped,
        )
    )
    try:
        await socket.serve()
    except WebSocketDisconnect:
        pass
    finally:
        # Its calls go on: parked, then handed to whoever holds the agent now, or left to wait for
        # the next process that registers it — a deploy is a socket leaving and another arriving.
        live.disconnect(socket.id)
        processes.closed(socket.id)
        parked = live.park(socket.id)
        await registry.release(socket.id)
        await handed_on(live, registry, parked)


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
        tuning: TuningStore,
        knowledge: Knowledge | None,
        processes: Processes,
    ):
        self._websocket = websocket
        self._id = a_socket_id()
        self.key = key
        self.logs = logs
        self.registry = registry
        self.live = live
        self.admission = admission
        self.tuning = tuning
        self.knowledge = knowledge
        self.processes = processes

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
        """Whose corner of that world: a developer's own in the sandbox, nobody's in production."""
        return held_by(self.key)

    @property
    def author(self) -> str:
        """Whom a row this socket writes names as its author: the person, else the key."""
        return author_of(self.key)

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

    async def stopped(self, why: str) -> None:
        """Tell the app it was stopped — it exits rather than reconnect — and close its socket."""
        said = ErrorEvent(code=STOPPED, message=why, recoverable=False)
        await self.send(unstored("error", said))
        await self._websocket.close(reason=as_a_close_reason(why))

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
        if command.type == DIAL:
            await self.refuse(command.agent, "no_handler", NOT_THIS_SOCKET, command.model_dump())
            return
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


# `call.dial` is agent-scoped, so it lands here rather than on a session — and this socket does not
# answer it. Placing a call is a door of its own, `POST /v1/agents/{slug}/dial`, because it opens a
# log and passes the outbound guards before any call exists, and because it is the `talk` scope's
# and not `app`'s: what holds an agent and what may ring a stranger's phone are two rights, and an
# app socket holds the first. Refused by name, so an app is not told it has no session when the
# reason is that it knocked in the wrong place. docs/protocol/console-api.md §4.
DIAL = "call.dial"
NOT_THIS_SOCKET = (
    "call.dial is not answered on the app socket: POST /v1/agents/{slug}/dial places a call, with"
    " the `talk` scope and the org's outbound guards"
)


# ── what this socket answers itself ─────────────────────────────────────────────
#
# The table and its decorator live in apps/handlers.py, with the call-scoped ones in text/.


@handles("agent.register")
async def register(socket: Socket, command: Command) -> None:
    """This socket speaks for this agent, or it is told why not. It brings no doors with it:
    `routes` is still on the wire so an app on an older package registers, and is not read —
    a door is a row an operator typed (api/numbers.py), and the widget is not a door."""
    wanted = asked(command, AgentRegister)
    socket.processes.named(socket.id, wanted.host)
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
        sdk=wanted.sdk,
        takes_unclaimed=wanted.takes_unclaimed,
    )
    await socket.send(entry)
    # The calls of this agent that the last process left behind are this one's now. A console
    # takes none: it never takes a call nobody named, and a live call is nobody's to name.
    if wanted.takes_unclaimed:
        await parked_calls_of(socket.live, (socket.env, socket.holder, command.agent), socket.id)


# A deploy is one process leaving and another arriving. The leaving one says so first, and its calls
# go to the socket that would take a new call of the agent — another process already running — or
# wait, parked, for the one that is starting. It keeps the agent until its socket closes, so the
# tools it is still running answer; it is sent no new tool, because it serves no call any more.
@handles("agent.drain")
async def drain(socket: Socket, command: Command) -> None:
    """Hand this socket's calls on and answer agent.draining with how many went where."""
    socket.registry.drain(socket.id, socket.env, command.agent)
    handed, parked = await handed_on(socket.live, socket.registry, socket.live.bound_to(socket.id))
    entry = await socket.registry.drained(socket.id, socket.env, command.agent, handed, parked)
    await socket.send(entry)


# What the agent reads is refused HERE, where the app is declaring itself, and not in a call where
# a turn would find nothing: the same rule that refuses a voice nobody curated. Two sentences, each
# naming the verb that fixes it. A class that searches its bases itself — `this.knowledge.search` —
# needs one attached in this world; and every base the world attaches has to have been pushed to
# it. The bases are the ones the session would read, from the resolver every session is built by:
# one rule for which bases, never a second copy of it here.
NO_BASE_ATTACHED = (
    "{slug} searches its bases, and none is attached to it in {world}: "
    "pinecall docs attach <base> --agent {slug}"
)
NO_SUCH_BASE = (
    "{slug} reads the base {base}, and nothing was pushed to {base} in {world}: pinecall docs push"
)


@handles("agent.configure")
async def configure(socket: Socket, command: Command) -> None:
    """Declare or change what the agent is. Only the fields the app sent change."""
    wanted = asked(command, AgentConfigure)
    held = socket.registry.on(socket.env, command.agent, socket.id)
    # Not held on this socket: the registry's own refusal below says so, in its own words.
    if held is not None:
        declared = declaration.configured(held.config, wanted.config)
        await the_bases_it_reads(socket, command.agent, declared)
    entry = await socket.registry.configure(socket.id, socket.env, command.agent, wanted.config)
    await socket.send(entry)


async def the_bases_it_reads(socket: Socket, slug: str, declared: AgentConfig) -> None:
    """Refused when the class searches with nothing attached, or reads a base never pushed."""
    session = await tuned_for(socket.tuning, socket.org, socket.env, socket.holder, slug, declared)
    bases = session.config.bases
    if declared.uses_knowledge and not bases:
        raise DeclarationRefused(NO_BASE_ATTACHED.format(slug=slug, world=socket.env))
    # A gateway with no table keeps no base at all, and there a lookup finds nothing and refuses
    # nobody (api/_deps.py): the same holds for the declaration.
    if socket.knowledge is None or not bases:
        return
    pushed = {
        kept.base for kept in await socket.knowledge.bases(socket.org, socket.env, socket.holder)
    }
    for docs in bases:
        if docs.base not in pushed:
            raise DeclarationRefused(
                NO_SUCH_BASE.format(slug=slug, base=docs.base, world=socket.env)
            )


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
