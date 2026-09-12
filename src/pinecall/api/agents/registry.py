"""Which sockets hold which agent and which doors, live, in which world; the record is the log."""

from __future__ import annotations

import dataclasses
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated
from uuid import uuid4

from fastapi import Depends
from starlette.requests import HTTPConnection

from pinecall.api._deps import held
from pinecall.log.entry import Entry
from pinecall.providers import declaration
from pinecall.types import PRODUCTION, AgentConfig, DeclarationRefused, Env, Route
from pinecall_protocol import WireModel, defs, encode
from pinecall_protocol.events import AgentConfigured, AgentDetached

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

# The name an agent is held under: its world, whose corner of that world, and its slug. Nobody's
# corner in production — what is deployed is the ORG's, held by the key its box runs on, and a
# person's key does not open `app` there at all (types/key.py). In development the member the key
# was minted for, so two developers of one tenant each hold their own `tienda-sur` and neither
# takes the other's; a development key that names nobody — CI's — holds the org's own, which is
# what a developer holding nothing falls back to. A DIALLED door is namespaced by none of this: a
# number exists once in a world and rings in one place. docs/decisions/dispatch.md.
type Held = tuple[Env, str | None, str]

# One world and one slug: what a dialled door answers for, and what a listing shows once.
type Agent = tuple[Env, str]


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
    # Whose corner of `env` this is: the member in development, nobody in production and nobody
    # for a development key that names none. See `Held` above. Last with the defaulted fields
    # rather than beside `env`, because nobody's corner is what almost every registration has.
    holder: str | None = None
    sdk: str | None = None
    # Whether a call that named no app may be handed to this socket. A console says no and stays a
    # full holder in every other way. See docs/decisions/dispatch.md.
    takes_unclaimed: bool = True
    # The order this process accepted the claim in. Two corners of one world may hold the same
    # slug, so "the newest holder" of a shared door has to be a number and not a dict's order.
    claimed: int = 0

    @property
    def held_as(self) -> Held:
        """The name this table keeps the agent under."""
        return (self.env, self.holder, self.slug)

    @property
    def agent(self) -> Agent:
        """The world and the slug: what a dialled door answers for, whoever is holding it."""
        return (self.env, self.slug)

    # The web route is deliberately left out: see docs/decisions/routes.md. Every agent's widget
    # would be the one door ("web", None), and the second agent of a fleet would be refused it.
    @property
    def dialled_doors(self) -> tuple[tuple[str, str | None], ...]:
        """The doors somebody dials: the pairs the registry keeps unique, one agent each."""
        return tuple(route.door for route in declaration.dialled(self.routes))


class Registry:
    """The gateway's live table. Every accepted claim is also appended to the agent's own log."""

    def __init__(self, logs: Logs) -> None:
        self._logs = logs
        # Many sockets may hold one agent — see docs/decisions/dispatch.md. The list is the order
        # they claimed it in, so the newest is the last, and a socket correcting its own doors
        # keeps its place: it is the same process, not a newer one.
        self._agents: dict[Held, list[Registration]] = {}
        # A dialled door is one agent's in one world: the number exists once in the world, so it
        # is keyed by the world and the slug and by nobody's corner — it is what refuses a
        # development key a production number, and what lets two developers of one tenant declare
        # the same number without taking it from each other's agent.
        self._at: dict[tuple[str, str | None], Agent] = {}
        self._owned: dict[SocketId, set[Held]] = {}
        # Counts accepted claims, so `answering` can say which of two corners took a door last.
        self._claims = 0

    # ── reading ─────────────────────────────────────────────────────────────────

    def of(self, env: Env, slug: str, holder: str | None = None) -> Registration | None:
        """The newest socket holding this agent for this holder, or the org's own when they hold
        none: what it declared, and its doors."""
        return self._newest((env, holder, slug)) or self._newest((env, None, slug))

    # Whoever is holding it, in whichever corner: what a number that rings reaches, and the one
    # read that asks for no holder at all. A dialled door is the org's, not a developer's.
    def answering(self, env: Env, slug: str) -> Registration | None:
        """The newest socket holding this agent in this world, whoever they are. None if nobody."""
        return max(
            (holding[-1] for name, holding in self._agents.items() if name[0::2] == (env, slug)),
            key=lambda held: held.claimed,
            default=None,
        )

    # What a door that RANG reaches: no key said whose corner, because a number is the org's. In
    # production there is one corner and this is `serving` with no app; in development it is
    # whichever developer started last, which is what a shared number being shared means.
    def taking(self, env: Env, slug: str) -> Registration | None:
        """Who takes a call that arrived at a door, in whichever corner. None when nobody does."""
        return max(
            (
                unclaimed
                for name in self._agents
                if name[0::2] == (env, slug)
                if (unclaimed := self._takes_unclaimed(name)) is not None
            ),
            key=lambda held: held.claimed,
            default=None,
        )

    def on(self, env: Env, slug: str, app: SocketId) -> Registration | None:
        """This agent as one named socket holds it, which is what `?app=` asks for. None if not."""
        for name in self._owned.get(app, ()):
            if name[0::2] != (env, slug):
                continue
            found = next((h for h in self._agents.get(name, ()) if h.owner == app), None)
            if found is not None:
                return found
        return None

    # THE one answer to "which process serves this call", asked by both doors that open one: the
    # chat door with `?app=`, and POST /v1/calls with the app id the worker was given. Two doors
    # asking it two ways would be two rules. A call that named no app skips every socket that takes
    # none, and only this question does: `of()` still means the newest holder, whatever it declared,
    # because a console alone still declares the agent and still serves its own call.
    # See docs/decisions/dispatch.md.
    def serving(
        self, env: Env, slug: str, app: SocketId | None, holder: str | None = None
    ) -> Registration | None:
        """Who takes a call: the socket it named, or the newest one that takes unclaimed calls."""
        if app is not None:
            return self.on(env, slug, app)
        return self._takes_unclaimed((env, holder, slug)) or self._takes_unclaimed(
            (env, None, slug)
        )

    # What this reader may REACH, once per (world, slug): their own corner and the org's own, the
    # two `of` falls through. Another developer's is left out: a row a click could not open would
    # be the 403 the rail exists to avoid.
    def holding(
        self, org: str, env: Env | None = None, holder: str | None = None
    ) -> tuple[Registration, ...]:
        """Every agent this org holds in that world — or in both, when none is named — once each."""
        seen: dict[Agent, Registration] = {}
        for (world, whose, slug), holding in self._agents.items():
            if holding[-1].org != org or (env is not None and world != env):
                continue
            if whose is not None and whose != holder:
                continue
            if (world, slug) not in seen or whose == holder:
                seen[(world, slug)] = holding[-1]
        return tuple(seen.values())

    # Every corner, because a quota is the ORG's: two developers holding two different agents are
    # two agents against the plan, and the same agent in both worlds is one.
    def slugs(self, org: str) -> frozenset[str]:
        """Every agent slug this org is holding anywhere right now, once each."""
        return frozenset(
            slug for (_, _, slug), holding in self._agents.items() if holding[-1].org == org
        )

    def routes(self, org: str, env: Env, holder: str | None = None) -> tuple[Route, ...]:
        """Every door this org answers in this world right now, as its agents claimed them."""
        return tuple(route for held in self.holding(org, env, holder) for route in held.routes)

    # Only a dialled door is in the table, so ("web", None) is None however many agents hold a
    # widget: a web arrival names its agent and never asks this. See docs/decisions/routes.md.
    def at(self, channel: str, number: str | None) -> Registration | None:
        """Who answers this door, in whichever world claimed it: a number rings in one place."""
        agent = self._at.get((channel, number))
        return None if agent is None else self.answering(*agent)

    # The one reader that knows a slug and no world: the sink, projecting an entry by the state
    # fields its agent declared. Production's declaration when the agent is held there, because
    # that is the one a stranger's log was written under; a laptop's otherwise.
    def declared(self, slug: str) -> AgentConfig | None:
        """What this agent declared, wherever it is held. None when no socket holds it at all."""
        held = self.answering(PRODUCTION, slug) or next(
            (holding[-1] for name, holding in self._agents.items() if name[2] == slug), None
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
        # Nobody's corner unless said: production has one, and so does a key naming no member.
        holder: str | None = None,
    ) -> Entry:
        """Add this socket to the agent's holders, take its doors, and write agent.registered."""
        await self._refuse_another_orgs_slug(org, slug)
        doors = [declaration.a_route(org, env, slug, route) for route in routes]
        self._refuse_a_taken_door((env, slug), doors)
        # This socket correcting its own doors keeps what it declared; a socket joining an agent
        # somebody else holds starts from what that agent already is, and corrects it with the
        # agent.configure one round trip later. A call landing in that window must not find an
        # agent with no instructions. See docs/decisions/dispatch.md.
        held = self.on(env, slug, owner) or self.of(env, slug, holder)
        config = held.config if held else declaration.an_agent(slug, doors)
        self._replace(
            Registration(
                slug=slug,
                org=org,
                env=env,
                holder=holder,
                owner=owner,
                routes=tuple(doors),
                config=dataclasses.replace(config, channels=frozenset(r.channel for r in doors)),
                sdk=sdk,
                takes_unclaimed=takes_unclaimed,
            )
        )
        said = declaration.registered(owner, doors, sdk, env)
        return await self._append(slug, "agent.registered", said)

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
            env, _, slug = name
            self._claim_doors((env, slug))
            detached = AgentDetached(app=owner, env=env, left=not left)
            await self._append(slug, "agent.detached", detached)
        return frozenset(slug for _, _, slug in released)

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

    # A number is one door in the world, so the table of doors is not namespaced by world or by
    # corner: the same agent in the OTHER world is a taker too, and a development key claiming a
    # production number is refused in a sentence that says which world holds it.
    def _refuse_a_taken_door(self, agent: Agent, routes: Sequence[Route]) -> None:
        """A number answers for one agent in one world at a time, and never twice in a register."""
        _, slug = agent
        claimed: set[tuple[str, str | None]] = set()
        for route in declaration.dialled(routes):
            if route.door in claimed:
                raise DeclarationRefused(f"agent {slug} claims the door {_said(route)} twice")
            claimed.add(route.door)
            taken = self._at.get(route.door)
            if taken is not None and taken != agent:
                taken_env, taken_slug = taken
                raise DeclarationRefused(
                    f"the door {_said(route)} already answers for agent {taken_slug} in {taken_env}"
                )

    # ── the table ───────────────────────────────────────────────────────────────

    def _replace(self, registration: Registration) -> None:
        """Commit a claim: this socket's place among the holders, its doors, what it now holds."""
        self._claims += 1
        claim = dataclasses.replace(registration, claimed=self._claims)
        holding = self._agents.setdefault(claim.held_as, [])
        for at, already in enumerate(holding):
            if already.owner == claim.owner:
                holding[at] = claim
                break
        else:
            holding.append(claim)
        self._claim_doors(claim.agent)
        self._owned.setdefault(claim.owner, set()).add(claim.held_as)

    def _newest(self, name: Held) -> Registration | None:
        """The last socket to claim this exact name, or nothing when none holds it."""
        holding = self._agents.get(name)
        return holding[-1] if holding else None

    def _takes_unclaimed(self, name: Held) -> Registration | None:
        """The newest holder of this name that answers a call nobody named. None when there is no
        such socket, which is what refuses a call into a terminal that only serves its own."""
        return next((h for h in reversed(self._agents.get(name, ())) if h.takes_unclaimed), None)

    # Keyed by the world and the slug, never by a corner: a developer running the agent locally
    # answers the shared development number until the next one starts, and neither is refused.
    def _claim_doors(self, agent: Agent) -> None:
        """The doors this agent answers are its newest socket's in this world, and only those."""
        held = self.answering(*agent)
        wanted: set[tuple[str, str | None]] = set(held.dialled_doors) if held else set()
        for door in [door for door, answered in self._at.items() if answered == agent]:
            if door not in wanted:
                del self._at[door]
        for door in wanted:
            self._at[door] = agent

    # Through the process's live log, not the store: a console holding the agent's SSE stream open
    # hears a register the moment it is accepted, instead of on its next reconnect.
    async def _append(self, slug: str, type: str, event: WireModel) -> Entry:
        """The claim is not accepted until the agent's own log says so; call is None, always."""
        return await self._logs.writing_agent(slug).append(type, encode(event))


def _said(route: Route) -> str:
    """A door, for a person: 'phone +34910000000'. Only a dialled door is ever refused."""
    return f"{route.channel} {route.number}"


# ── how a route asks for it ─────────────────────────────────────────────────────


def the_registry(connection: HTTPConnection) -> Registry:
    """Who owns which agent and which doors right now."""
    return held(connection, "registry", Registry)


RegistryDep = Annotated[Registry, Depends(the_registry)]
