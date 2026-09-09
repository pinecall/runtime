"""What only this process knows, half two: the sockets and calls open here (see log/writers.py)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends

from pinecall.api._deps import what_is_live
from pinecall.api.agents.registry import Send, SocketId
from pinecall.filling import OpenCall
from pinecall.log.fanout import Subscription
from pinecall.log.logs import CallLog
from pinecall.session.pending import ToolCalls
from pinecall.session.text.session import TextSession
from pinecall.types import AgentConfig, CallContext
from pinecall_protocol import Command
from pinecall_protocol.defs import ToolResult


# One call the app is being shown, whichever process runs it: the app socket it was bound to when
# it opened, the subscription that carries its entries down that socket, and the queue a command
# waits in for the worker that will apply it. `None` in that queue is the call ending, which is the
# only way the worker's stream stops. The context and the config are what the door that opened
# the call knew of it, kept so a fill and a hang-up can ask whose contact and which base it is.
@dataclass(frozen=True)
class Served:
    """A live call from the app socket's side: whose it is, what it is fed, what it asked for."""

    agent: str
    org: str
    app: SocketId | None
    entries: Subscription
    commands: asyncio.Queue[Command | None]
    context: CallContext
    config: AgentConfig


# None of this is durable and none of it should be: it is a fact about which sockets are open right
# now, not a fact about the world. The world is the log.
class Live:
    """This process's memory: who is connected, which calls run here, what they are waiting on."""

    def __init__(self) -> None:
        self._apps: dict[SocketId, Send] = {}
        self._sessions: dict[str, TextSession] = {}
        self._waiting: dict[str, ToolCalls] = {}
        self._served: dict[str, Served] = {}
        # The loop keeps no strong reference to a task: a pump nobody holds can be collected in
        # the middle of a call, and the app simply stops hearing it.
        self._pumps: set[asyncio.Task[None]] = set()

    # ── the app sockets ─────────────────────────────────────────────────────────

    def connect(self, owner: SocketId, send: Send) -> None:
        """This app socket is open and can be handed the entries of the calls it answers."""
        self._apps[owner] = send

    def disconnect(self, owner: SocketId) -> None:
        """The app socket is gone. Its calls keep running until the caller hangs up."""
        self._apps.pop(owner, None)

    # ── every call, whoever runs it ─────────────────────────────────────────────

    # THE one registration: a text call (api/calls/chat.py) and a call a worker opened
    # (POST /v1/calls) are put on this same delivery, so an app hears both alike. What carries the
    # entries is the log's own fanout, which is what keeps the log the single truth: an entry
    # reaches the app because it was written, never because somebody remembered to send it too.
    def serve(
        self,
        call: str,
        agent: str,
        org: str,
        log: CallLog,
        app: SocketId | None,
        *,
        context: CallContext,
        config: AgentConfig,
    ) -> None:
        """Every entry of this call to the ONE app socket its door chose, for the whole call."""
        if call in self._served:
            return
        # Which socket is the door's answer, never this table's: `registry.serving()` decided it
        # once, and nothing here ever resolves an agent to a socket again — so a register that
        # lands mid-call cannot move a live conversation. See docs/decisions/dispatch.md.
        entries = log.subscribe()
        self._served[call] = Served(
            agent=agent,
            org=org,
            app=app,
            entries=entries,
            commands=asyncio.Queue(),
            context=context,
            config=config,
        )
        send = None if app is None else self._apps.get(app)
        if send is None:
            entries.close()
            return
        pump = asyncio.ensure_future(_feeding(entries, send))
        self._pumps.add(pump)
        pump.add_done_callback(self._pumps.discard)

    # The concurrent-calls quota counts THIS: calls served by this process right now, whatever
    # runs them. A head row never sealed — a worker that died — would count for ever; a served
    # call is forgotten the moment its door closes it.
    def running(self, org: str) -> int:
        """How many of this org's calls are open here right now."""
        return sum(1 for served in self._served.values() if served.org == org)

    # The write doors ask this instead of the head row: the org was said once, at the door that
    # opened the call, and a worker appending to a call is appending to one this process serves.
    def org_of(self, call: str) -> str | None:
        """Whose call this is, as the door that opened it said; None when none is served."""
        served = self._served.get(call)
        return None if served is None else served.org

    # What the fill and the hang-up ask of a call, and the only thing they ask: the org, how the
    # call arrived and what its agent declared, as the door that opened it said. filling/ holds
    # the Protocol; this is its one implementation.
    def the_call(self, call: str) -> OpenCall | None:
        """The call as a fill sees it, or None when this gateway is not serving it."""
        served = self._served.get(call)
        if served is None:
            return None
        return OpenCall(org=served.org, context=served.context, config=served.config)

    def close(self, call: str) -> None:
        """The call is over and nothing more will be said on it."""
        self._sessions.pop(call, None)
        self._waiting.pop(call, None)
        served = self._served.pop(call, None)
        if served is not None:
            # What is already queued still reaches the app, and the worker's stream ends.
            served.entries.close()
            served.commands.put_nowait(None)

    # ── the calls this process runs itself ──────────────────────────────────────

    def open(self, session: TextSession) -> None:
        """A text call started here: the app's call-scoped commands can now find it."""
        self._sessions[session.call] = session

    def of(self, call: str | None) -> TextSession | None:
        """The session a command names, or None when no call by that id runs in this process."""
        return None if call is None else self._sessions.get(call)

    # ── the calls a worker runs ─────────────────────────────────────────────────

    # A voice call runs in the worker process, so this gateway holds none of its session — only
    # the two things it alone can hold: the app socket a tool has to travel down, and the commands
    # the app sends back, which the worker reads from GET /v1/calls/{call}/commands.
    def waiting(self, call: str, config: AgentConfig) -> ToolCalls:
        """The waiting room of a worker-run call, opened the first time a tool of it goes out."""
        waiting = self._waiting.get(call)
        if waiting is None:
            waiting = self._waiting[call] = ToolCalls(config)
        return waiting

    def answered(self, call: str, result: ToolResult) -> bool:
        """The app answered a tool of a worker-run call; False when nobody here was waiting."""
        waiting = self._waiting.get(call)
        return waiting is not None and waiting.answered(result)

    def commanded(self, call: str | None, agent: str, command: Command) -> bool:
        """Hold a command for the worker running this call. False when no such call is served."""
        served = None if call is None else self._served.get(call)
        if served is None or served.agent != agent:
            return False
        served.commands.put_nowait(command)
        return True

    def commands(self, call: str) -> asyncio.Queue[Command | None] | None:
        """What the app said about this call, for the worker to read; None when none is served."""
        served = self._served.get(call)
        return None if served is None else served.commands

    def app_of(self, call: str) -> SocketId | None:
        """The app socket this call was bound to when it opened. Nothing ever moves it."""
        served = self._served.get(call)
        return None if served is None else served.app


async def _feeding(entries: Subscription, send: Send) -> None:
    """One call's entries down an app's socket, until the log ends or the socket does."""
    try:
        async for entry in entries:
            await send(entry)
    except Exception:  # noqa: BLE001 — an app that went away must never break the call's log
        entries.close()


# ── how a route asks for it ─────────────────────────────────────────────────────


# The dep itself lives in api/_deps.py, so the app socket can ask for the very same object
# without importing this module: a test that overrides it answers both doors at once.
LiveDep = Annotated[Live, Depends(what_is_live)]
