"""Which sockets hold which agent and which doors, live, in which world; the record is the log."""

from __future__ import annotations

import dataclasses
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any
from uuid import uuid4

from fastapi import Depends
from starlette.requests import HTTPConnection

from pinecall.api._deps import held
from pinecall.log.entry import Entry
from pinecall.providers import declaration
from pinecall.types import PRODUCTION, AgentConfig, DeclarationRefused, Env, Route
from pinecall.types.channel import CHANNELS_WITH_A_NUMBER
from pinecall_protocol import WireModel, defs, encode
from pinecall_protocol.events import AgentConfigured, AgentDetached, AgentRegistered

# The type only, and never at import time: api/calls/ reads this module through the sink, so
# naming its package here for real would close the circle. See docs/decisions/api.md.
if TYPE_CHECKING:
    from pinecall.log.writers import Logs

# Nobody is holding this agent right now. 404 and not 409: from the asking side the agent does not
# exist, and a call on it ends before the caller has spoken. Here and not in a route module because
# both doors that answer it — the worker's config door and the agent's pipeline door — need it.
NO_AGENT = "no app is holding agent {slug}"

# A caller or a worker named one app socket, and that socket is not holding this agent: it has
# disconnected, it never registered the agent, or its key is another org's — one answer, because
# an agent answers for one org. Both doors that open a call refuse in these words.
NOT_THAT_APP = "app {app} is not holding agent {slug}: it disconnected, or it never held it"

# Sockets ARE holding this agent, and every one of them registered with takes_unclaimed false: a
# console — `pinecall chat`, `pinecall talk` — holds the agent to serve the call it opens itself.
# A call nobody claimed is refused here rather than dropped into whoever's terminal is open, which
# is the whole point of the flag; the sentence says what to start so the agent answers in public.
NO_UNCLAIMED = (
    "agent {slug} is held only by apps that take no call they did not open: start `pinecall run`"
)

# A socket's id is minted, not id(websocket): it travels to the app in agent.registered and comes
# back on `?app=`, and CPython reuses an address the moment the object at it is collected — a stale
# one would name a socket somebody else now holds. Named HERE, and imported by handlers.py and by
# api/_live.py, because handlers.py imports this module — the alias in handlers would have
# made this module import handlers back, which is the cycle handlers.py exists to prevent.
type SocketId = str

_AN_APP = "app_"


def a_socket_id() -> SocketId:
    """One connected app, told apart from every other for as long as this process runs."""
    return f"{_AN_APP}{uuid4().hex[:12]}"


# How an entry reaches somebody who is reading a call.
type Send = Callable[[Entry], Awaitable[None]]

# The name an agent is held under: its world, then its slug. The same slug is held once in each
# world, by different sockets, and neither sees the other — a laptop's `pinecall run` on a dev key
# and the box's on a production key are two agents to this table and one log to the store.
type Held = tuple[Env, str]


@dataclass(frozen=True)
class Registration:
    """One agent as ONE socket holds it: whose it is, where, which doors, what it declared."""

    slug: str
    org: str
    # The world the key that registered it opens: every door here and every call it takes is
    # that world's, and call.started says so.
    env: Env
    owner: SocketId
    routes: tuple[Route, ...]
    config: AgentConfig
    sdk: str | None = None
    # Whether a call that named no app may be handed to this socket. A console says no and stays a
    # full holder in every other way. See docs/decisions/dispatch.md.
    takes_unclaimed: bool = True

    @property
    def held_as(self) -> Held:
        """The name this table keeps the agent under."""
        return (self.env, self.slug)

    # The web route is deliberately left out: see docs/decisions/routes.md. Every agent's widget
    # would be the one door ("web", None), and the second agent of a fleet would be refused it.
    @property
    def dialled_doors(self) -> tuple[tuple[str, str | None], ...]:
        """The doors somebody dials: the pairs the registry keeps unique, one agent each."""
        return tuple(route.door for route in _dialled(self.routes))


class Registry:
    """The gateway's live table. Every accepted claim is also appended to the agent's own log."""

    def __init__(self, logs: Logs) -> None:
        self._logs = logs
        # Many sockets may hold one agent — see docs/decisions/dispatch.md. The list is the order
        # they claimed it in, so the newest is the last, and a socket correcting its own doors
        # keeps its place: it is the same process, not a newer one.
        self._agents: dict[Held, list[Registration]] = {}
        # A dialled door is one agent's in one world: the number exists once in the world, so the
        # table is not namespaced here — it is what refuses a development key a production number.
        self._at: dict[tuple[str, str | None], Held] = {}
        self._owned: dict[SocketId, set[Held]] = {}

    # ── reading ─────────────────────────────────────────────────────────────────

    def of(self, env: Env, slug: str) -> Registration | None:
        """The newest socket holding this agent in this world: what it declared, its doors."""
        holding = self._agents.get((env, slug))
        return holding[-1] if holding else None

    def on(self, env: Env, slug: str, app: SocketId) -> Registration | None:
        """This agent as one named socket holds it, which is what `?app=` asks for. None if not."""
        return next((held for held in self._agents.get((env, slug), ()) if held.owner == app), None)

    # THE one answer to "which process serves this call", asked by both doors that open one: the
    # chat door with `?app=`, and POST /v1/calls with the app id the worker was given. Two doors
    # asking it two ways would be two rules. A call that named no app skips every socket that takes
    # none, and only this question does: `of()` still means the newest holder, whatever it declared,
    # because a console alone still declares the agent and still serves its own call.
    # See docs/decisions/dispatch.md.
    def serving(self, env: Env, slug: str, app: SocketId | None) -> Registration | None:
        """Who takes a call: the socket it named, or the newest one that takes unclaimed calls."""
        if app is not None:
            return self.on(env, slug, app)
        holding = self._agents.get((env, slug), ())
        return next((held for held in reversed(holding) if held.takes_unclaimed), None)

    def holding(self, org: str, env: Env | None = None) -> tuple[Registration, ...]:
        """Every agent this org holds in that world — or in both, when none is named — once each."""
        return tuple(
            holding[-1]
            for (world, _), holding in self._agents.items()
            if holding[-1].org == org and (env is None or world == env)
        )

    def routes(self, org: str, env: Env) -> tuple[Route, ...]:
        """Every door this org answers in this world right now, as its agents claimed them."""
        return tuple(route for held in self.holding(org, env) for route in held.routes)

    # Only a dialled door is in the table, so ("web", None) is None however many agents hold a
    # widget: a web arrival names its agent and never asks this. See docs/decisions/routes.md.
    def at(self, channel: str, number: str | None) -> Registration | None:
        """Who answers this door, in whichever world claimed it: a number rings in one place."""
        held = self._at.get((channel, number))
        return None if held is None else self.of(*held)

    # The one reader that knows a slug and no world: the sink, projecting an entry by the state
    # fields its agent declared. Production's declaration when the agent is held there, because
    # that is the one a stranger's log was written under; a laptop's otherwise.
    def declared(self, slug: str) -> AgentConfig | None:
        """What this agent declared, wherever it is held. None when no socket holds it at all."""
        held = self.of(PRODUCTION, slug) or next(
            (holding[-1] for (_, name), holding in self._agents.items() if name == slug), None
        )
        return None if held is None else held.config

    # ── claiming ────────────────────────────────────────────────────────────────

    async def register(
        self,
        owner: SocketId,
        org: str,
        env: Env,
        slug: str,
        routes: Sequence[defs.Route],
        sdk: str | None = None,
        takes_unclaimed: bool = True,
    ) -> Entry:
        """Add this socket to the agent's holders, take its doors, and write agent.registered."""
        await self._refuse_another_orgs_slug(org, slug)
        doors = [declaration.a_route(org, env, slug, route) for route in routes]
        self._refuse_a_taken_door((env, slug), doors)
        # This socket correcting its own doors keeps what it declared; a socket joining an agent
        # somebody else holds starts from what that agent already is, and corrects it with the
        # agent.configure one round trip later. A call landing in that window must not find an
        # agent with no instructions. See docs/decisions/dispatch.md.
        held = self.on(env, slug, owner) or self.of(env, slug)
        config = held.config if held else declaration.an_agent(slug, doors)
        self._replace(
            Registration(
                slug=slug,
                org=org,
                env=env,
                owner=owner,
                routes=tuple(doors),
                config=dataclasses.replace(config, channels=frozenset(r.channel for r in doors)),
                sdk=sdk,
                takes_unclaimed=takes_unclaimed,
            )
        )
        return await self._append(slug, "agent.registered", _registered(owner, doors, sdk, env))

    async def configure(
        self, owner: SocketId, env: Env, slug: str, wire: defs.AgentConfig
    ) -> Entry:
        """Apply the fields this configure carries, and write agent.configured to its log."""
        held = self.on(env, slug, owner)
        if held is None:
            raise DeclarationRefused(
                f"agent {slug} is not registered on this socket: register it before configuring it"
            )
        self._replace(dataclasses.replace(held, config=declaration.configured(held.config, wire)))
        configured = AgentConfigured(changed=list(declaration.changed_by(wire)))
        return await self._append(slug, "agent.configured", configured)

    # The other half of register, and written down like it: a console reading the floor saw
    # processes arrive and never leave until agent.detached said so — which socket, which world,
    # and whether the agent is held there by anybody still.
    async def release(self, owner: SocketId) -> frozenset[str]:
        """This socket is gone: it stops holding its agents, and whoever is left keeps them."""
        released = self._owned.pop(owner, set())
        for name in released:
            left = [held for held in self._agents.get(name, ()) if held.owner != owner]
            if left:
                self._agents[name] = left
            else:
                self._agents.pop(name, None)
            self._claim_doors(name)
            env, slug = name
            detached = AgentDetached(app=owner, env=env, left=not left)
            await self._append(slug, "agent.detached", detached)
        return frozenset(slug for _, slug in released)

    # ── the rules ───────────────────────────────────────────────────────────────

    # Durable, not live: the slug's owner is on its own log's head row, so an org that registered
    # `clinica-norte` last month still owns it today with no socket open, and a second org that
    # picks the same word is refused before it writes a line into the first one's log. One log
    # per slug, whatever the world: the entries say which world each claim and each call was.
    async def _refuse_another_orgs_slug(self, org: str, slug: str) -> None:
        """A slug is one org's: the first to register it, for as long as its log exists."""
        owner = await self._logs.owner(None, slug)
        if owner is not None and owner != org:
            raise DeclarationRefused(f"agent {slug} belongs to another org: a slug is one org's")
        await self._logs.owned(None, slug, org)

    # A number is one door in the world, so the table of doors is not namespaced: the same agent
    # in the OTHER world is a taker too, and a development key claiming a production number is
    # refused in a sentence that says which world holds it.
    def _refuse_a_taken_door(self, name: Held, routes: Sequence[Route]) -> None:
        """A number answers for one agent in one world at a time, and never twice in a register."""
        _, slug = name
        claimed: set[tuple[str, str | None]] = set()
        for route in _dialled(routes):
            if route.door in claimed:
                raise DeclarationRefused(f"agent {slug} claims the door {_said(route)} twice")
            claimed.add(route.door)
            taken = self._at.get(route.door)
            if taken is not None and taken != name:
                taken_env, taken_slug = taken
                raise DeclarationRefused(
                    f"the door {_said(route)} already answers for agent {taken_slug} in {taken_env}"
                )

    # ── the table ───────────────────────────────────────────────────────────────

    def _replace(self, registration: Registration) -> None:
        """Commit a claim: this socket's place among the holders, its doors, what it now holds."""
        holding = self._agents.setdefault(registration.held_as, [])
        for at, already in enumerate(holding):
            if already.owner == registration.owner:
                holding[at] = registration
                break
        else:
            holding.append(registration)
        self._claim_doors(registration.held_as)
        self._owned.setdefault(registration.owner, set()).add(registration.held_as)

    def _claim_doors(self, name: Held) -> None:
        """The doors this agent answers are its newest socket's, and only those."""
        held = self.of(*name)
        wanted: set[tuple[str, str | None]] = set(held.dialled_doors) if held else set()
        for door in [door for door, answering in self._at.items() if answering == name]:
            if door not in wanted:
                del self._at[door]
        for door in wanted:
            self._at[door] = name

    # Through the process's live log, not the store: a console holding the agent's SSE stream open
    # hears a register the moment it is accepted, instead of on its next reconnect.
    async def _append(self, slug: str, type: str, event: WireModel) -> Entry:
        """The claim is not accepted until the agent's own log says so; call is None, always."""
        return await self._logs.writing_agent(slug).append(type, encode(event))


# encode() drops what nobody set, so an optional field is left out here rather than sent as null:
# the schema says `label` is a string when it is there, and null is not a string.
def _registered(
    owner: SocketId, routes: Sequence[Route], sdk: str | None, env: Env
) -> AgentRegistered:
    """The agent.registered payload: this socket's id, the doors as the wire says them, the SDK."""
    said: dict[str, Any] = {"app": owner, "routes": [_wire(route) for route in routes], "env": env}
    if sdk is not None:
        said["sdk"] = sdk
    return AgentRegistered(**said)


def _wire(route: Route) -> defs.Route:
    """The domain's route as the wire says it back: the door, without the org that owns it."""
    door: dict[str, Any] = {"channel": route.channel, "number": route.number}
    if route.label is not None:
        door["label"] = route.label
    return defs.Route(**door)


def _said(route: Route) -> str:
    """A door, for a person: 'phone +34910000000'. Only a dialled door is ever refused."""
    return f"{route.channel} {route.number}"


def _dialled(routes: Sequence[Route]) -> tuple[Route, ...]:
    """The routes somebody dials. A web route names no door: what identifies it is its agent."""
    return tuple(route for route in routes if route.channel in CHANNELS_WITH_A_NUMBER)


# ── how a route asks for it ─────────────────────────────────────────────────────


def the_registry(connection: HTTPConnection) -> Registry:
    """Who owns which agent and which doors right now."""
    return held(connection, "registry", Registry)


RegistryDep = Annotated[Registry, Depends(the_registry)]
