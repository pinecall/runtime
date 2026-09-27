"""What the gateway keeps in memory: who holds each agent, the line, and the calls it serves."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from cryptography.fernet import MultiFernet
from livekit import api

from pinecall.channels.routes import room_closed, rooms_with_an_agent
from pinecall.domain.errors import Conflict, DeclarationRefused, NotAvailable, QuotaExhausted
from pinecall.domain.types import (
    CHANNELS_WITH_A_NUMBER,
    PRODUCTION,
    THE_ORGS_OWN,
    AgentConfig,
    CallContext,
    Corner,
    Env,
    JsonObject,
    Route,
    Versions,
    today_in,
)
from pinecall.log import index
from pinecall.log.log import Log, Logs, Subscription, arrival_entry
from pinecall.log.reduce import reduce
from pinecall.log.store import Claim, Store
from pinecall.postgres.pool import Pool
from pinecall.providers import catalog, prices
from pinecall.providers.build import Running
from pinecall.providers.catalog import Providers
from pinecall.providers.declared import apply_tuning
from pinecall.providers.keys import Keys, thinking
from pinecall.session import text
from pinecall.session.call import Call, Platform
from pinecall.session.session import Session, written
from pinecall.session.tools import ToolCalls
from pinecall.tenancy import admission, corners, orgs, vault
from pinecall.tenancy.agents import Codes
from pinecall.wire.commands import DevAnswer
from pinecall.wire.events import (
    TERMINAL_EVENT,
    AgentConfigured,
    AgentDetached,
    AgentDraining,
    AgentRegistered,
    CallAttached,
    CallClaimed,
    CallEnded,
    CallScore,
    CallStarted,
    CallSummary,
    CreditsExhausted,
)
from pinecall.wire.frames import Command, Entry
from pinecall.wire.metrics import LLMModelUsage, ModelUsage
from pinecall.wire.parts import EndReason, PlatformTool
from pinecall.wire.rest import Sealing
from pinecall.wire.state import AgentTurn

logger = logging.getLogger(__name__)

# Minted, never id(websocket): CPython reuses addresses, and the id travels back as `?app=`.
type SocketId = str
type Send = Callable[[Entry], Awaitable[None]]
type Stop = Callable[[str], Awaitable[None]]
# A slug is one org's: the corner names the org, the world and whose copy it is.
type Held = tuple[Corner, str]

AN_APP = "app_"

NO_AGENT = "no app is holding agent {slug}"
NOT_THAT_APP = "app {app} is not holding agent {slug}: it disconnected, or it never held it"
NO_UNCLAIMED = (
    "agent {slug} is held only by apps that take no call they did not open: run `pinecall start`"
)
NOT_HOLDING = "agent {slug} is not held in {env} by an app of yours that answers an unclaimed call"
ANOTHER_ORGS = "agent {slug} belongs to another org: a slug is one org's"
NOT_REGISTERED = "agent {slug} is not registered on this socket: register it first"
NO_LOOKUPS = "this runtime keeps no memory and no knowledge base to look in"
NOT_JUDGED = "no judge ran on this call"
NOT_JUDGED_REAPED = (
    "the worker holding this call went away before it could end it, and the platform sealed the "
    "log: there was no session left to judge"
)
NOTHING_SAID = "no reply"


def new_socket_id() -> SocketId:
    """A new id for an app socket."""
    return f"{AN_APP}{uuid4().hex[:12]}"


# ── who holds each agent ──


@dataclass(frozen=True)
class Registration:
    """One agent as one app socket holds it."""

    slug: str
    corner: Corner
    owner: SocketId
    config: AgentConfig
    sdk: str | None = None
    # False for a console: it takes only the calls that name it.
    takes_unclaimed: bool = True
    # The order of claims across corners, so the newest holder is known.
    claimed: int = 0
    # Set by agent.drain: its tools still answer, it takes no new call.
    draining: bool = False

    @property
    def held_as(self) -> Held:
        """The registry's key for this agent in its corner."""
        return (self.corner, self.slug)


# Durable facts are in the log; this is which sockets are open now. The line is where a ring
# lands: the org's own corner in production, one developer's in the sandbox, which an org shares.
class Registry:
    """The agents the app sockets hold, per corner, the line of each, and the developers' phones."""

    def __init__(self, logs: Logs) -> None:
        """Nobody holds anything yet."""
        self.logs = logs
        self.holders: dict[Held, list[Registration]] = {}
        self.lines: dict[tuple[Env, str], str] = {}
        self.phones: dict[tuple[Env, str], str] = {}
        self.owned: dict[SocketId, set[Held]] = {}
        self.claims = 0

    # ── reading ──

    def of(self, corner: Corner, slug: str) -> Registration | None:
        """The newest holder in this corner, else in the org's own."""
        return self._newest((corner, slug)) or self._newest((_own(corner), slug))

    def serving(self, corner: Corner, slug: str, app: SocketId | None) -> Registration | None:
        """The socket a call opened in this corner goes to: the one named, else the newest taker."""
        if app is not None:
            held = self.on(app, corner.env, slug)
            return held if held is not None and held.corner.org == corner.org else None
        return self._taker((corner, slug)) or self._taker((_own(corner), slug))

    # A caller's registered phone reaches their own corner first; then the line.
    def taking(self, corner: Corner, slug: str, caller: str | None) -> Registration | None:
        """The socket a ring at the agent lands in."""
        if caller is not None and (holder := self.phones.get((corner.env, caller))) is not None:
            theirs = self._taker((replace(corner, holder=holder), slug))
            if theirs is not None:
                return theirs
        holder = self.lines.get((corner.env, slug))
        if holder is None:
            return None
        return self._taker((replace(corner, holder=holder), slug))

    def on(self, app: SocketId, env: Env, slug: str) -> Registration | None:
        """The agent as this socket holds it in this world."""
        for held in self.owned.get(app, ()):
            if held[0].env == env and held[1] == slug:
                return next((one for one in self.holders.get(held, ()) if one.owner == app), None)
        return None

    def owned_by(self, app: SocketId) -> list[Registration]:
        """Every agent one socket holds, in claim order."""
        held = (one for name in self.owned.get(app, ()) for one in self.holders.get(name, ()))
        return sorted((one for one in held if one.owner == app), key=lambda one: one.claimed)

    # One row per slug, the reader's corner over the org's; every corner for a team reader.
    def holding(self, corner: Corner, *, every_corner: bool) -> list[Registration]:
        """The org's held agents in the world, as this reader sees them."""
        seen: dict[str | Held, Registration] = {}
        for (whose, slug), holding in self.holders.items():
            if whose.org != corner.org or whose.env != corner.env or not holding:
                continue
            mine = whose.holder in (THE_ORGS_OWN, corner.holder)
            if not every_corner and not mine:
                continue
            under: str | Held = (whose, slug) if every_corner else slug
            if under not in seen or whose.holder == corner.holder:
                seen[under] = holding[-1]
        return list(seen.values())

    def slugs(self, org: str) -> frozenset[str]:
        """Every slug the org holds, in any world and corner: the agents quota counts these."""
        return frozenset(
            slug for (whose, slug), held in self.holders.items() if whose.org == org and held
        )

    def held_anywhere(self, slug: str) -> bool:
        """Whether any socket holds the slug."""
        return any(held and name[1] == slug for name, held in self.holders.items())

    # A log reader knows the slug alone: production's declaration, else any world's.
    def declared(self, slug: str) -> AgentConfig | None:
        """The agent's declared config, production's first."""
        found = sorted(
            (held[-1] for (_, name), held in self.holders.items() if name == slug and held),
            key=lambda one: (one.corner.env != PRODUCTION, -one.claimed),
        )
        return found[0].config if found else None

    # ── the line ──

    def line_of(self, env: Env, slug: str) -> str | None:
        """The corner holding the agent's line; None when nobody does."""
        return self.lines.get((env, slug))

    # Taking the line is said out loud: starting later never takes a colleague's calls.
    def take_the_line(self, corner: Corner, slug: str) -> Registration:
        """Give the agent's line to this corner, which must hold an app that answers a ring."""
        taking = self._taker((corner, slug))
        if taking is None:
            raise DeclarationRefused(NOT_HOLDING.format(slug=slug, env=corner.env))
        self.lines[(corner.env, slug)] = corner.holder
        return taking

    def drop_the_line(self, corner: Corner, slug: str) -> bool:
        """Let the line go, to the newest other corner that could take it."""
        if self.lines.get((corner.env, slug)) != corner.holder:
            return False
        del self.lines[(corner.env, slug)]
        self._next_takes_the_line(corner, slug, leaving=corner.holder)
        return True

    def waiting_for_the_line(self, corner: Corner, slug: str) -> list[Registration]:
        """Every corner of the org that could take the line, newest first."""
        taking = [
            one
            for (whose, name) in self.holders
            if name == slug and whose.org == corner.org and whose.env == corner.env
            if (one := self._taker((whose, name))) is not None
        ]
        return sorted(taking, key=lambda one: one.claimed, reverse=True)

    # ── the developers' phones ──

    def calls_from(self, env: Env, number: str, holder: str) -> None:
        """Send rings from this phone to this person's corner; the last one said wins."""
        self.phones[(env, number)] = holder

    def forget_calls_from(self, env: Env, holder: str) -> list[str]:
        """Forget every phone of this person, and say which."""
        gone = sorted(
            n for (world, n), whose in self.phones.items() if world == env and whose == holder
        )
        for number in gone:
            del self.phones[(env, number)]
        return gone

    def calling(self, env: Env, holder: str) -> list[str]:
        """The phones this person rings from."""
        return sorted(
            n for (world, n), whose in self.phones.items() if world == env and whose == holder
        )

    # ── claiming ──

    async def register(
        self, owner: SocketId, corner: Corner, slug: str, *, sdk: str | None, takes_unclaimed: bool
    ) -> Entry:
        """This socket holds the agent from now on; agent.registered is written on its log."""
        store = self.logs.store
        owner_org = await store.owner(None, slug)
        if owner_org is not None and owner_org != corner.org:
            raise DeclarationRefused(ANOTHER_ORGS.format(slug=slug))
        await store.claim(None, slug, corner.org)
        # A call that arrives before agent.configure still runs on what the agent declared.
        held = self.on(owner, corner.env, slug) or self.of(corner, slug)
        config = held.config if held is not None else AgentConfig(slug=slug)
        self._replace(
            Registration(
                slug=slug,
                corner=corner,
                owner=owner,
                config=config,
                sdk=sdk,
                takes_unclaimed=takes_unclaimed,
            )
        )
        said = AgentRegistered(routes=[], app=owner, sdk=sdk, env=corner.env)
        return await self._written(corner.env, slug, "agent.registered", said.written())

    async def configure(
        self, owner: SocketId, env: Env, slug: str, config: AgentConfig, changed: Sequence[str]
    ) -> Entry:
        """The declaration this socket sent; agent.configured names what changed."""
        held = self.on(owner, env, slug)
        if held is None:
            raise DeclarationRefused(NOT_REGISTERED.format(slug=slug))
        self._replace(replace(held, config=config))
        said = AgentConfigured(changed=list(changed))
        return await self._written(env, slug, "agent.configured", said.written())

    # A deploy: the socket keeps the agent so its tools still answer, and takes no new call.
    def drain(self, owner: SocketId, env: Env, slug: str) -> Registration:
        """Mark the socket as draining the agent."""
        held = self.on(owner, env, slug)
        if held is None:
            raise DeclarationRefused(NOT_REGISTERED.format(slug=slug))
        draining = replace(held, draining=True)
        self._replace(draining)
        return draining

    async def drained(
        self, owner: SocketId, env: Env, slug: str, *, handed: int, parked: int
    ) -> Entry:
        """agent.draining, once the socket's calls moved."""
        said = AgentDraining(app=owner, env=env, handed=handed, parked=parked)
        return await self._written(env, slug, "agent.draining", said.written())

    async def release(self, owner: SocketId) -> None:
        """A socket closed: its agents let go, the line handed on, agent.detached for each."""
        for held in self.owned.pop(owner, set()):
            left = [one for one in self.holders.get(held, ()) if one.owner != owner]
            if left:
                self.holders[held] = left
            else:
                self.holders.pop(held, None)
            corner, slug = held
            if self.lines.get((corner.env, slug)) == corner.holder and not left:
                del self.lines[(corner.env, slug)]
                self._next_takes_the_line(corner, slug)
            said = AgentDetached(app=owner, env=corner.env, left=not left)
            await self._written(corner.env, slug, "agent.detached", said.written())

    def _replace(self, registration: Registration) -> None:
        self.claims += 1
        claim = replace(registration, claimed=self.claims)
        holding = self.holders.setdefault(claim.held_as, [])
        at = next((n for n, one in enumerate(holding) if one.owner == claim.owner), None)
        if at is None:
            holding.append(claim)
        else:
            holding[at] = claim
        # The first corner able to take a ring gets the line; later ones claim it.
        if claim.takes_unclaimed and not claim.draining:
            self.lines.setdefault((claim.corner.env, claim.slug), claim.corner.holder)
        self.owned.setdefault(claim.owner, set()).add(claim.held_as)

    def _newest(self, held: Held) -> Registration | None:
        holding = self.holders.get(held)
        return holding[-1] if holding else None

    def _taker(self, held: Held) -> Registration | None:
        holding = reversed(self.holders.get(held, ()))
        return next((one for one in holding if one.takes_unclaimed and not one.draining), None)

    def _next_takes_the_line(
        self, corner: Corner, slug: str, *, leaving: str | None = None
    ) -> None:
        waiting = [
            one for one in self.waiting_for_the_line(corner, slug) if one.corner.holder != leaving
        ]
        if waiting:
            self.lines[(corner.env, slug)] = waiting[0].corner.holder

    # A laptop restarts often: in the sandbox these entries are forgettable, or they flood the log.
    async def _written(self, env: Env, slug: str, kind: str, said: JsonObject) -> Entry:
        forgettable = None if env == PRODUCTION else True
        return await self.logs.agent(slug).append(kind, said, ephemeral=forgettable)


# ── the calls served ──


@dataclass(frozen=True)
class Process:
    """One connected app socket, as the org's list of processes shows it."""

    app: SocketId
    corner: Corner
    address: str | None
    connected_at: float
    # Sends `error stopped` and closes: the SDK exits instead of reconnecting.
    stop: Stop
    host: str | None = None


# A call belongs to its agent, not to a socket: `app` is None while it waits for one.
@dataclass(frozen=True)
class Served:
    """A call this gateway serves: the socket it reaches, its log, its commands, its tools."""

    agent: str
    corner: Corner
    app: SocketId | None
    log: Log
    entries: Subscription
    # A None ends the worker's command stream.
    commands: asyncio.Queue[Command | None]
    context: CallContext
    config: AgentConfig
    tools: ToolCalls
    # A written call runs here; a voice call runs in a worker.
    session: Session | None = None

    @property
    def call(self) -> str:
        """The call's id."""
        return self.context.call


# Not durable on purpose: it says which sockets are open now; the log is the record.
class Live:
    """The open app sockets and the calls served to them."""

    def __init__(self) -> None:
        """Nothing open."""
        self.sockets: dict[SocketId, Send] = {}
        self.processes: dict[SocketId, Process] = {}
        self.calls: dict[str, Served] = {}
        self.asked: dict[str, asyncio.Future[DevAnswer]] = {}
        # asyncio keeps weak references to tasks: the pumps are held here.
        self.pumps: set[asyncio.Task[None]] = set()

    def connect(self, process: Process, send: Send) -> None:
        """An app socket opened."""
        self.sockets[process.app] = send
        self.processes[process.app] = process

    def disconnect(self, app: SocketId) -> None:
        """An app socket closed; its calls go on until another socket takes them."""
        self.sockets.pop(app, None)
        self.processes.pop(app, None)

    def named(self, app: SocketId, host: str | None) -> None:
        """The machine the app said it runs on."""
        process = self.processes.get(app)
        if process is not None and host is not None:
            self.processes[app] = replace(process, host=host)

    def processes_of(self, org: str, env: Env) -> list[Process]:
        """The org's sockets in the world, oldest first."""
        mine = (
            one
            for one in self.processes.values()
            if one.corner.org == org and one.corner.env == env
        )
        return sorted(mine, key=lambda one: one.connected_at)

    # dev.request is never stored: it goes straight down the socket.
    async def tell(self, app: SocketId, entry: Entry) -> bool:
        """Send an entry to one socket; False when it is not open here."""
        send = self.sockets.get(app)
        if send is None:
            return False
        await send(entry)
        return True

    def ask(self, asked: str) -> asyncio.Future[DevAnswer]:
        """A future for the answer to a dev.request."""
        answer: asyncio.Future[DevAnswer] = asyncio.get_running_loop().create_future()
        self.asked[asked] = answer
        return answer

    def dev_answered(self, answer: DevAnswer) -> bool:
        """Hand an app's dev.answer to the door waiting for it; False when nobody waits."""
        waiting = self.asked.pop(answer.id, None)
        if waiting is None or waiting.done():
            return False
        waiting.set_result(answer)
        return True

    def serve(self, served: Served) -> None:
        """Serve the call to the socket its door chose; a call already served stays as it is."""
        if served.call in self.calls:
            return
        self.calls[served.call] = served
        self._feed(served)

    # Synchronous, so two sockets taking one parked call cannot both have it.
    def attach(self, call: str, app: SocketId | None) -> Served | None:
        """Move the call to this socket (None parks it); None when nothing moved."""
        served = self.calls.get(call)
        if served is None or served.app == app:
            return None
        served.entries.close()
        moved = replace(served, app=app, entries=served.log.fanout.subscribe())
        self.calls[call] = moved
        self._feed(moved)
        return moved

    def bound_to(self, app: SocketId) -> list[str]:
        """The calls this socket serves."""
        return [call for call, served in self.calls.items() if served.app == app]

    def parked(self, corner: Corner, agent: str) -> list[str]:
        """The calls of the agent in the corner that wait for a socket."""
        return [
            call
            for call, served in self.calls.items()
            if served.app is None and served.agent == agent and served.corner == corner
        ]

    # The concurrent calls quota counts these, never head rows: a dead worker's row would count
    # for ever.
    def running(self, org: str) -> int:
        """How many of the org's calls are open here."""
        return sum(1 for served in self.calls.values() if served.corner.org == org)

    def commanded(self, call: str | None, agent: str, command: Command) -> bool:
        """Queue an app's command for the worker running the call; False when it is not here."""
        served = None if call is None else self.calls.get(call)
        if served is None or served.agent != agent or served.session is not None:
            return False
        served.commands.put_nowait(command)
        return True

    def close(self, call: str) -> None:
        """Forget a finished call: its entries end, and its worker's command stream."""
        served = self.calls.pop(call, None)
        if served is None:
            return
        served.entries.close()
        served.commands.put_nowait(None)

    def _feed(self, served: Served) -> None:
        send = None if served.app is None else self.sockets.get(served.app)
        if send is None:
            served.entries.close()
            return
        pump = asyncio.ensure_future(_pumped(served.entries, send))
        self.pumps.add(pump)
        pump.add_done_callback(self.pumps.discard)


async def _pumped(entries: Subscription, send: Send) -> None:
    try:
        async for entry in entries:
            await send(entry)
    except (OSError, RuntimeError):
        logger.warning("an app socket stopped taking its call's entries", exc_info=True)
        entries.close()


# ── opening a call ──


@dataclass(frozen=True)
class Gated:
    """What a written call runs through: the tables, the vault, the logs, and the live calls."""

    pool: Pool
    vault: MultiFernet
    logs: Logs
    live: Live


# A call opened with a key goes to that key's corner; one that rang, to the caller's phone
# owner, else to the line.
def serving_agent(
    registry: Registry, corner: Corner, agent: str, app: SocketId | None, context: CallContext
) -> Registration | None:
    """The socket a new call of the agent reaches."""
    if app is None and context.route.channel in CHANNELS_WITH_A_NUMBER:
        return registry.taking(_own(corner), agent, context.caller)
    return registry.serving(corner, agent, app)


def served_call(
    gated: Gated,
    owner: SocketId | None,
    context: CallContext,
    config: AgentConfig,
    corner: Corner,
) -> Served:
    """Serve the call to its socket, or park it for the next one; before its first entry."""
    log = gated.logs.writing(context.call, config.slug)
    served = Served(
        agent=config.slug,
        corner=corner,
        app=owner,
        log=log,
        entries=log.fanout.subscribe(),
        commands=asyncio.Queue(),
        context=context,
        config=config,
        tools=ToolCalls(config, log.append),
    )
    gated.live.serve(served)
    return gated.live.calls[context.call]


async def opened(log: Log, context: CallContext, agent: str) -> None:
    """call.ringing for an inbound call; an outbound one has its call.dialing already."""
    if context.direction == "outbound":
        return
    kind, said = arrival_entry(context, context.route.number or agent)
    await log.append(kind, said)


async def tuned(
    pool: Pool, declared: AgentConfig, corner: Corner, configured: Providers
) -> tuple[AgentConfig, Versions]:
    """The declaration under the corner's settings, and the versions it was built from."""
    standing = await corners.standing(pool, corner, declared.slug)
    config = apply_tuning(declared, standing.tuning, standing.lexicon, defaults=configured.defaults)
    return config, standing.versions


# Read per call, so a rotated key runs from the next call on.
async def keys_of(pool: Pool, sealed: MultiFernet, corner: Corner) -> Keys:
    """What the org's calls in the world may run on."""
    quotas = await orgs.quotas_of(pool, corner.org, corner.env)
    return Keys(
        own=await vault.credentials_of(pool, sealed, corner.org),
        box=await vault.box_credentials(pool, sealed),
        lends=quotas.lends,
    )


# The refusal is written on the agent's log as the numbers the quota ran out at.
async def exhausted(logs: Logs, org: str, agent: str, refused: QuotaExhausted) -> None:
    """credits.exhausted on the agent's log, for a refusal that names its quota."""
    if refused.quota is None:
        return
    said = CreditsExhausted.model_validate(
        {"org": org, "quota": refused.quota, "used": refused.used, "limit": refused.limit}
    )
    await logs.agent(agent).append("credits.exhausted", said.written())


# Admission before the log is claimed: a refused call leaves nothing behind.
async def open_text(gated: Gated, held: Registration, context: CallContext) -> Session:
    """A new written call on the socket that holds the agent, admitted and unstarted."""
    pool, corner = gated.pool, held.corner
    configured = await catalog.providers(pool)
    config, versions = await tuned(pool, held.config, corner, configured)
    model = thinking(config, configured, await keys_of(pool, gated.vault, corner))
    await admission.admit_call(pool, corner.org, corner.env, running=gated.live.running(corner.org))
    await gated.logs.store.claim(context.call, held.slug, corner.org, Claim(corner, versions))
    served = served_call(gated, held.owner, context, config, corner)
    return _session(gated, served, model)


# A gateway that restarted forgot the call, not the caller: no admission, no second greeting.
async def resume_text(gated: Gated, held: Registration, call: str, zone: str) -> Session | None:
    """A written call taken up again from its log; None for one that is over or not this agent's."""
    kept = await index.corner_of_call(gated.pool, call)
    if kept is None or kept.sealed or kept.corner is None or kept.agent != held.slug:
        return None
    if kept.corner.org != held.corner.org:
        return None
    entries = await gated.logs.store.whole(call)
    if not entries or any(entry.type == TERMINAL_EVENT for entry in entries):
        return None
    configured = await catalog.providers(gated.pool)
    config, _ = await tuned(gated.pool, held.config, kept.corner, configured)
    model = thinking(config, configured, await keys_of(gated.pool, gated.vault, kept.corner))
    context = _as_it_opened(call, held, kept.corner, entries, zone)
    served = served_call(gated, None, context, config, kept.corner)
    session = _session(gated, served, model)
    await text.resume(session, text.taken_up(entries))
    await attach(gated.live, gated.logs.store, call, held.owner)
    return session


def _session(gated: Gated, served: Served, model: Running) -> Session:
    async def seal(usage: list[ModelUsage], outcome: str) -> None:
        await sealed(gated, served, Sealing(usage=usage, outcome=outcome))

    platform = Platform(
        append=served.log.append, tool=served.tools.ran, lookup=nothing_found, seal=seal
    )
    session = written(Call(served.context, served.config, platform), model)
    gated.live.calls[served.call] = replace(served, session=session)
    return session


async def nothing_found(_tool: PlatformTool, _asked: JsonObject, _speech: str | None) -> JsonObject:
    """A lookup on a runtime with no memory and no knowledge."""
    raise NotAvailable(NO_LOOKUPS)


def _as_it_opened(
    call: str, held: Registration, corner: Corner, entries: Iterable[Entry], zone: str
) -> CallContext:
    started = next((one for one in entries if one.type == "call.started"), None)
    said = None if started is None else CallStarted.model_validate(started.data)
    caller = "" if said is None else (said.from_ or "")
    # A WhatsApp conversation comes back on its number; a chat at none.
    channel = "web" if said is None else said.channel
    number = said.to if said is not None and channel in CHANNELS_WITH_A_NUMBER else None
    route = Route(org=corner.org, agent=held.slug, channel=channel, number=number, env=corner.env)
    today = (
        today_in(zone) if said is None else datetime.fromtimestamp(said.started_at, tz=UTC).date()
    )
    return CallContext(
        call=call,
        channel=channel,
        direction="inbound",
        caller=caller or call,
        route=route,
        today=today,
        holder=corner.holder or None,
    )


# ── a call changing hands ──

# Entries a taking-over socket is rebuilt from; the prompt is not, the log keeps its hash alone.
STARTED = "call.started"
CLAIMED = "call.claimed"


async def attach(live: Live, store: Store, call: str, app: SocketId) -> Entry | None:
    """Give the call to this socket: call.attached first, then the tools still waiting."""
    served = live.attach(call, app)
    if served is None:
        return None
    entries = await store.whole(call)
    started = next((one for one in entries if one.type == STARTED), None)
    if started is None:
        return None
    claimed = next(
        (str(one.data["code"]) for one in reversed(entries) if one.type == CLAIMED), None
    )
    said = CallAttached(
        app=app,
        started=CallStarted.model_validate(started.data),
        state=reduce(entries).app_state,
        seq=entries[-1].seq,
        claimed=claimed,
    )
    entry = await served.log.append("call.attached", said.written())
    # On the same queue, after call.attached, so the result lands on the call id still awaited.
    for waiting in served.tools.pending():
        served.entries.offer(waiting)
    return entry


async def parked_calls_of(
    live: Live, store: Store, corner: Corner, slug: str, app: SocketId
) -> list[str]:
    """Give every parked call of the agent in the corner to this socket."""
    parked = live.parked(corner, slug)
    return [call for call in parked if await attach(live, store, call, app) is not None]


# Each call of a leaving socket goes where a new call would, or waits parked.
async def handed_on(
    live: Live, store: Store, registry: Registry, calls: Iterable[str]
) -> tuple[int, int]:
    """Hand the calls on; how many were handed and how many parked."""
    handed = parked = 0
    for call in calls:
        served = live.calls.get(call)
        if served is None:
            continue
        taking = registry.serving(served.corner, served.agent, None)
        if taking is not None and await attach(live, store, call, taking.owner) is not None:
            handed += 1
        else:
            live.attach(call, None)
            parked += 1
    return handed, parked


async def claim_code(
    codes: Codes, served: Served, code: str, *, via: Literal["keypad", "agent"]
) -> bool:
    """Tie the call to the page showing the code; False when no page waits on it."""
    issued = await codes.claim(served.corner.env, served.agent, code, served.call)
    if issued is None:
        return False
    await served.log.append("call.claimed", CallClaimed(code=code, via=via).written())
    return True


# ── the end of a call ──


# One end for every call: a worker's, a written one's, and one the reaper finishes.
async def sealed(
    gated: Gated, served: Served, sealing: Sealing, *, lent: Sequence[str] = ()
) -> None:
    """Price the call, write its summary and its score, seal the log, let the call go."""
    await summed_up(gated.pool, gated.logs.store, served.log, sealing)
    if lent:
        await index.lent(gated.pool, served.call, lent)
    score = CallScore(judges=[], judge_calls=0, not_judged=NOT_JUDGED)
    await served.log.append("call.score", score.written())
    gated.logs.forget(served.call)
    gated.live.close(served.call)


async def summed_up(pool: Pool, store: Store, log: Log, sealing: Sealing) -> None:
    """call.summary: how the call ended, what it used, what that cost."""
    entries = await store.whole(log.name)
    ended = next((one for one in reversed(entries) if one.type == "call.ended"), None)
    over = None if ended is None else CallEnded.model_validate(ended.data)
    state = reduce(entries)
    summary = CallSummary(
        reason="error" if over is None else over.reason,
        outcome=sealing.outcome,
        duration_s=0.0 if over is None else over.duration_s,
        turns=sum(1 for turn in state.turns if isinstance(turn, AgentTurn)),
        usage=sealing.usage,
        cost=prices.cost(sealing.usage, await catalog.providers(pool)),
        recording=sealing.recording,
    )
    await log.append("call.summary", summary.written())


def tokens_of(usage: Iterable[ModelUsage]) -> int:
    """The model tokens a call used so far: the llm_tokens quota counts these."""
    return sum(
        (one.input_tokens or 0) + (one.output_tokens or 0)
        for one in usage
        if isinstance(one, LLMModelUsage)
    )


# ── the reaper ──

# Well over livekit's empty_timeout (60 s): the empty room is the signal, this is the margin.
QUIET_S = 5 * 60.0
REAPED_EVERY_S = 60.0
AT_MOST = 100
# How long a WhatsApp thread waits for its contact before it is closed.
A_THREAD_WAITS_S = 2 * 60 * 60.0
REAPED = "sealed %s: no agent is in its room and it has said nothing for %.0f s"


# A killed job writes no call.ended, and nothing else would close its log.
async def reaped(gated: Gated, server: api.LiveKitAPI, now: float) -> list[str]:
    """Seal the quiet calls nothing runs any more, and say which."""
    sealed_now: list[str] = []
    quiet = await index.unsealed_spoken(gated.pool, now - QUIET_S, limit=AT_MOST)
    standing: set[str] = set()
    if quiet:
        standing = await rooms_with_an_agent(server, [one.call for one in quiet])
    for orphan in quiet:
        if orphan.call not in standing and await _finished(gated, orphan, "drained"):
            # The room goes too, so whoever is still in it hears the call end.
            await room_closed(server, orphan.call)
            logger.warning(REAPED, orphan.call, now - orphan.last_at)
            sealed_now.append(orphan.call)
    for orphan in await index.unsealed_written(gated.pool, now - QUIET_S, limit=AT_MOST):
        # A written call waits for its caller as long as its channel would.
        patience = A_THREAD_WAITS_S if orphan.channel == "whatsapp" else QUIET_S
        if gated.live.calls.get(orphan.call) is not None or now - orphan.last_at < patience:
            continue
        if await _finished(gated, orphan, "timeout"):
            sealed_now.append(orphan.call)
    return sealed_now


# Duration ends at the last entry, not now: reaping late bills no extra minutes.
async def _finished(gated: Gated, orphan: index.Unsealed, reason: EndReason) -> bool:
    store = gated.logs.store
    log = gated.logs.writing(orphan.call, orphan.agent)
    written_types = {one.type for one in await store.whole(orphan.call)}
    try:
        if "call.ended" not in written_types:
            ended = CallEnded(
                reason=reason,
                ended_by="platform",
                ended_at=orphan.last_at,
                duration_s=max(orphan.last_at - orphan.started_at, 0.0),
            )
            await log.append("call.ended", ended.written())
        if "call.summary" not in written_types:
            await summed_up(gated.pool, store, log, Sealing(usage=[], outcome=NOTHING_SAID))
        score = CallScore(judges=[], judge_calls=0, not_judged=NOT_JUDGED_REAPED)
        await log.append("call.score", score.written())
    except Conflict:
        # Another gateway sealed it first: nothing is left to do.
        return False
    finally:
        gated.logs.forget(orphan.call)
    gated.live.close(orphan.call)
    return True


# A pass that fails is said, and the next one runs: the reaper never stops.
async def reap_forever(gated: Gated, server: api.LiveKitAPI) -> None:
    """A pass now, and one every minute."""
    while True:
        try:
            await reaped(gated, server, time.time())
        except (Conflict, NotAvailable, api.TwirpError, OSError):
            logger.warning(
                "the reaper's pass failed; the next is in %.0f s", REAPED_EVERY_S, exc_info=True
            )
        await asyncio.sleep(REAPED_EVERY_S)


def _own(corner: Corner) -> Corner:
    return replace(corner, holder=THE_ORGS_OWN)
