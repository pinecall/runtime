"""What only this process knows, half two: the sockets and calls open here (see log/writers.py)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import Annotated

from fastapi import Depends

from pinecall.api._deps import what_is_live
from pinecall.api.agents.holding import Send, SocketId
from pinecall.log.entry import Entry
from pinecall.log.fanout import Subscription
from pinecall.log.logs import CallLog
from pinecall.lookups import OpenCall
from pinecall.session.pending import ToolCalls
from pinecall.session.text.session import TextSession
from pinecall.types import AgentConfig, CallContext, Env
from pinecall_protocol import Command
from pinecall_protocol.commands import DevAnswer
from pinecall_protocol.defs import ToolResult


# One call the app is being shown, whichever process runs it: the app socket serving it now (None
# while it is parked, waiting for one), the subscription that carries its entries down that
# socket, and the queue a command waits in for the worker that will apply it. `None` in that
# queue is the call ending, which is the only way the worker's stream stops. The context and the
# config are what the door that opened the call knew of it, kept so a lookup and a hang-up can ask
# whose contact and which base it is.
@dataclass(frozen=True)
class Served:
    """A live call from the app socket's side: whose it is, what it is fed, what it asked for."""

    agent: str
    org: str
    app: SocketId | None
    # The call's log, kept so the entries can be subscribed to again for the next socket.
    log: CallLog
    entries: Subscription
    commands: asyncio.Queue[Command | None]
    context: CallContext
    config: AgentConfig
    # Whose corner of the world serves it, as the door that opened the call resolved it: the
    # developer in the sandbox, nobody in production. What this call recalls and searches is that
    # corner's, so a test call on one laptop never reads what another laptop's test call wrote.
    holder: str | None = None


# None of this is durable and none of it should be: it is a fact about which sockets are open right
# now, not a fact about the world. The world is the log.
class Live:
    """This process's memory: who is connected, which calls run here, what they are waiting on."""

    def __init__(self) -> None:
        self._apps: dict[SocketId, Send] = {}
        self._sessions: dict[str, TextSession] = {}
        self._waiting: dict[str, ToolCalls] = {}
        self._served: dict[str, Served] = {}
        # The console's asks of an app, waiting for that app's dev.answer, by the id the gateway
        # minted. A future and not a queue: one ask, one answer, and the door awaiting it.
        self._asked: dict[str, asyncio.Future[DevAnswer]] = {}
        # The loop keeps no strong reference to a task: a pump nobody holds can be collected in
        # the middle of a call, and the app simply stops hearing it.
        self._pumps: set[asyncio.Task[None]] = set()

    # ── the app sockets ─────────────────────────────────────────────────────────

    def connect(self, owner: SocketId, send: Send) -> None:
        """This app socket is open and can be handed the entries of the calls it answers."""
        self._apps[owner] = send

    def disconnect(self, owner: SocketId) -> None:
        """The app socket is gone. Its calls keep running, parked until a socket adopts them."""
        self._apps.pop(owner, None)

    # ── the console's asks of an app ────────────────────────────────────────────

    # A dev.request is a fact about two processes talking and not about the world, so it goes down
    # the one socket the door chose and is stored nowhere: the log is the truth, and this is not.
    async def tell(self, owner: SocketId, entry: Entry) -> bool:
        """One unstored entry down one app socket. False when that socket is not open here."""
        send = self._apps.get(owner)
        if send is None:
            return False
        await send(entry)
        return True

    def asked(self, id: str) -> asyncio.Future[DevAnswer]:
        """The answer a dev.request by this id will get, awaited by the door that sent it."""
        answer: asyncio.Future[DevAnswer] = asyncio.get_running_loop().create_future()
        self._asked[id] = answer
        return answer

    def forget_asked(self, id: str) -> None:
        """The door stopped waiting — it timed out, or the app went — and nothing lands here now."""
        self._asked.pop(id, None)

    def dev_answered(self, answer: DevAnswer) -> bool:
        """The app answered a dev.request; False when no door here is waiting on that id."""
        waiting = self._asked.pop(answer.id, None)
        if waiting is None or waiting.done():
            return False
        waiting.set_result(answer)
        return True

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
        holder: str | None = None,
    ) -> None:
        """Every entry of this call to the ONE app socket its door chose, for the whole call."""
        if call in self._served:
            return
        # Which socket is the door's answer, never this table's: `registry.serving()` decided it,
        # and the only thing that moves a live call to another socket is api/calls/attaching.py,
        # which writes call.attached so the log says who serves it from then on.
        entries = log.subscribe()
        self._served[call] = Served(
            agent=agent,
            org=org,
            app=app,
            log=log,
            entries=entries,
            commands=asyncio.Queue(),
            context=context,
            config=config,
            holder=holder,
        )
        self._feed(entries, app)

    def _feed(self, entries: Subscription, app: SocketId | None) -> None:
        """Pump a call's entries down that socket; close them when it is not open here."""
        send = None if app is None else self._apps.get(app)
        if send is None:
            entries.close()
            return
        pump = asyncio.ensure_future(_feeding(entries, send))
        self._pumps.add(pump)
        pump.add_done_callback(self._pumps.discard)

    def served(self, call: str) -> Served | None:
        """The call as this process serves it, or None when it serves no call by that id."""
        return self._served.get(call)

    # Synchronous, so the claim is made before anything awaits: two doors that both find a call
    # parked cannot both attach it. A socket already serving it is no change, and answers None.
    def attach(self, call: str, app: SocketId | None) -> Served | None:
        """Serve a live call from this socket from now on (None parks it); None if nothing moved."""
        served = self._served.get(call)
        if served is None or served.app == app:
            return None
        served.entries.close()
        moved = replace(served, app=app, entries=served.log.subscribe())
        self._served[call] = moved
        self._feed(moved.entries, app)
        return moved

    def park(self, owner: SocketId) -> list[str]:
        """Every call this socket served, parked: served by nobody until a socket adopts it."""
        calls = self.bound_to(owner)
        for call in calls:
            self.attach(call, None)
        return calls

    def bound_to(self, owner: SocketId) -> list[str]:
        """The calls this socket serves right now."""
        return [call for call, served in self._served.items() if served.app == owner]

    def parked(self, env: Env, holder: str | None, agent: str) -> list[str]:
        """The live calls of that agent, in that corner, that no socket serves."""
        return [
            call
            for call, served in self._served.items()
            if served.app is None
            and served.agent == agent
            and served.holder == holder
            and served.context.env == env
        ]

    def pending_tools(self, call: str) -> tuple[Entry, ...]:
        """The tool.call entries of this call still waiting for the app, whoever runs the call."""
        session = self._sessions.get(call)
        if session is not None:
            return session.pending_tools()
        waiting = self._waiting.get(call)
        return () if waiting is None else waiting.pending()

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

    # What a lookup and the hang-up ask of a call, and the only thing they ask: the org, how the
    # call arrived and what its agent declared, as the door that opened it said. lookups/ holds
    # the Protocol; this is its one implementation.
    def the_call(self, call: str) -> OpenCall | None:
        """The call as a lookup sees it, or None when this gateway is not serving it."""
        served = self._served.get(call)
        if served is None:
            return None
        return OpenCall(
            org=served.org,
            context=served.context,
            config=served.config,
            holder=served.holder,
        )

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
        """The app socket serving this call now: the one it opened on, or the last to adopt it."""
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
