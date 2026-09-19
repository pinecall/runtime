"""Which sockets hold which agent and which doors, live, in which world; the record is the log."""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends
from starlette.requests import HTTPConnection

from pinecall.api._deps import held
from pinecall.api.agents.doors import Agent, Doors
from pinecall.api.agents.holding import Held, Registration, SocketId
from pinecall.log.entry import Entry
from pinecall.providers import declaration
from pinecall.types import PRODUCTION, AgentConfig, DeclarationRefused, Env, Route, is_a_deployment
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
    "agent {slug} is held only by apps that take no call they did not open: run `pinecall start`"
)

# A corner asked for the line of an agent it is not holding, or holds only in a console. A ring
# lands on the line, so handing it to a corner with no app in it would drop the call.
NOT_HOLDING = "agent {slug} is not held in {env} by an app of yours that answers an unclaimed call"


class Registry:
    """The gateway's live table. Every accepted claim is also appended to the agent's own log."""

    def __init__(self, logs: Logs) -> None:
        self._logs = logs
        # Many sockets may hold one agent — see docs/decisions/dispatch.md. The list is the order
        # they claimed it in, so the newest is the last, and a socket correcting its own doors
        # keeps its place: it is the same process, not a newer one.
        self._agents: dict[Held, list[Registration]] = {}
        # The public side of the table: which agent each dialled door answers for, and whose
        # corner of the world its ring goes to. See api/agents/doors.py.
        self._doors = Doors()
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

    # What a door that RANG reaches. No key says whose corner, because a number is the ORG's: the
    # worker that dialled it holds a key naming nobody. Two questions, in this order — WHOSE phone
    # dialled, which a developer answers once and never thinks about again, and then the LINE,
    # which is what a number nobody claimed falls back to. In production the first never answers
    # and the second is the only corner there is.
    def taking(self, env: Env, slug: str, caller: str | None = None) -> Registration | None:
        """Who takes a call that arrived at a door. None when nobody would answer it."""
        theirs = self._the_callers_own(env, slug, caller)
        return theirs or self.serving(env, slug, None, self.line_for(env, slug))

    # A corner that registered the number but is not holding THIS agent falls through to the line
    # rather than refusing: the developer is not running it, and a call that reaches nobody because
    # of a setting they made last week is the worst answer available.
    def _the_callers_own(self, env: Env, slug: str, caller: str | None) -> Registration | None:
        """The corner whose own phone dialled, when it is holding this agent. None otherwise."""
        if caller is None:
            return None
        whose = self._doors.whose_call(env, caller)
        return None if whose is None else self._takes_unclaimed((env, whose, slug))

    def calls_from(self, env: Env, caller: str, holder: str) -> None:
        """Calls this number makes reach this corner, in whatever agent the corner is holding."""
        self._doors.calls_from(env, caller, holder)

    def forget_calls_from(self, env: Env, holder: str) -> tuple[str, ...]:
        """This corner stops answering its own calls. The numbers it had, for the answer."""
        return self._doors.forget_calls_from(env, holder)

    def calling(self, env: Env, holder: str | None) -> tuple[str, ...]:
        """The numbers whose calls reach this corner: what a terminal prints back at a person."""
        return self._doors.calling(env, holder)

    def line_for(self, env: Env, slug: str) -> str | None:
        """Whose corner the ring goes to. Nobody's corner is None too: ask `has_a_line` first."""
        return self._doors.line((env, slug))

    def has_a_line(self, env: Env, slug: str) -> bool:
        """Whether any corner is answering this agent's ring at all."""
        return self._doors.claimed((env, slug))

    # A second developer running the same agent does not take the first one's calls by starting
    # later; they say so. The refusal names what is missing, because a claim on an agent this
    # corner is not holding would ring in a terminal with no app in it.
    def take_the_line(self, env: Env, slug: str, holder: str | None) -> Registration:
        """Hand this agent's ringing doors to this corner, whoever had them."""
        taking = self._takes_unclaimed((env, holder, slug))
        if taking is None:
            raise DeclarationRefused(NOT_HOLDING.format(slug=slug, env=env))
        self._doors.take((env, slug), holder)
        return taking

    def drop_the_line(self, env: Env, slug: str, holder: str | None) -> bool:
        """This corner stops answering the ring, and the next corner still holding it picks up."""
        if not self._doors.release((env, slug), holder):
            return False
        self._the_next_corner_answers(env, slug)
        return True

    def waiting_for_the_line(self, env: Env, slug: str) -> tuple[Registration, ...]:
        """Every corner holding this agent that could take the line, newest claim first."""
        taking = (
            unclaimed
            for name in self._agents
            if name[0::2] == (env, slug)
            if (unclaimed := self._takes_unclaimed(name)) is not None
        )
        return tuple(sorted(taking, key=lambda held: held.claimed, reverse=True))

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
    # Two readings of one table. A developer asks "what can I open", and the answer is their own
    # corner with the org's underneath, one row per slug: two copies of `tienda-sur` are one entry
    # for them and the one they are running wins. An admin and the box operator ask "what is the
    # team running", and collapsing would hide the very thing they opened the page for — so they
    # get one row per CORNER, each saying whose it is. api/agents/endpoints.py decides which.
    def holding(
        self,
        org: str,
        env: Env | None = None,
        holder: str | None = None,
        *,
        every_corner: bool = False,
    ) -> tuple[Registration, ...]:
        """Every agent this org holds in that world — or in both, when none is named — once each,
        or once per corner for a reader who sees the whole team."""
        seen: dict[Held | Agent, Registration] = {}
        for name, holding in self._agents.items():
            world, whose, slug = name
            if holding[-1].org != org or (env is not None and world != env):
                continue
            if not every_corner and whose is not None and whose != holder:
                continue
            under: Held | Agent = name if every_corner else (world, slug)
            if under not in seen or whose == holder:
                seen[under] = holding[-1]
        return tuple(seen.values())

    # Every corner, because a quota is the ORG's: two developers holding two different agents are
    # two agents against the plan, and the same agent in both worlds is one.
    def slugs(self, org: str) -> frozenset[str]:
        """Every agent slug this org is holding anywhere right now, once each."""
        return frozenset(
            slug for (_, _, slug), holding in self._agents.items() if holding[-1].org == org
        )

    # Asked before an agent is moved between orgs: a socket that is holding it right now believes
    # what it registered with, and moving the log under it would leave the process and the table
    # disagreeing about whose agent this is until somebody restarts. `orgs move` refuses instead.
    def held_anywhere(self, slug: str) -> bool:
        """Whether any corner of any world is holding this slug at this instant."""
        return any(held == slug for (_, _, held) in self._agents)

    def routes(self, org: str, env: Env, holder: str | None = None) -> tuple[Route, ...]:
        """Every door this org answers in this world right now, as its agents claimed them."""
        return tuple(route for held in self.holding(org, env, holder) for route in held.routes)

    # Only a dialled door is in the table, so ("web", None) is None however many agents hold a
    # widget: a web arrival names its agent and never asks this. See docs/decisions/routes.md.
    def at(self, channel: str, number: str | None) -> Registration | None:
        """Who answers this door, in whichever world claimed it: a number rings in one place."""
        agent = self._doors.at((channel, number))
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
        self._doors.refuse_a_taken_one((env, slug), doors)
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
        return await self._append(slug, "agent.registered", said, env)

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
            env, holder, slug = name
            self._claim_doors((env, slug))
            # The corner that was answering the ring is gone. Whoever is still holding the agent
            # picks it up — that is not "the newest wins", because it happens only when the
            # terminal that HAD the line closed.
            if self._doors.release((env, slug), holder):
                self._the_next_corner_answers(env, slug)
            detached = AgentDetached(app=owner, env=env, left=not left)
            await self._append(slug, "agent.detached", detached, env)
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
        # Alone, nobody claims anything: the first corner to hold the agent answers its ring, and
        # every corner after it has to say so. A console takes no call it did not open, so it is
        # never handed a line it would not pick up.
        if claim.takes_unclaimed:
            self._doors.take_if_free(claim.agent, claim.holder)
        self._owned.setdefault(claim.owner, set()).add(claim.held_as)

    def _newest(self, name: Held) -> Registration | None:
        """The last socket to claim this exact name, or nothing when none holds it."""
        holding = self._agents.get(name)
        return holding[-1] if holding else None

    def _takes_unclaimed(self, name: Held) -> Registration | None:
        """The newest holder of this name that answers a call nobody named. None when there is no
        such socket, which is what refuses a call into a terminal that only serves its own."""
        return next((h for h in reversed(self._agents.get(name, ())) if h.takes_unclaimed), None)

    # Keyed by the world and the slug, never by a corner: a number is the org's door, and both
    # developers of one tenant declare it without taking it from each other. WHO picks it up is
    # the line, which `_the_next_corner_answers` and `take_the_line` are about.
    def _claim_doors(self, agent: Agent) -> None:
        """The doors this agent answers are its newest socket's in this world, and only those."""
        held = self.answering(*agent)
        self._doors.answered_by(agent, held.routes if held else ())

    def _the_next_corner_answers(self, env: Env, slug: str) -> None:
        """With the line free, the newest corner that takes an unclaimed call picks it up."""
        left = self.waiting_for_the_line(env, slug)
        if left:
            self._doors.take((env, slug), left[0].holder)

    # Through the process's live log, not the store: a console holding the agent's SSE stream open
    # hears a register the moment it is accepted, instead of on its next reconnect.
    # Who is holding an agent RIGHT NOW is live state, not history — and in a sandbox it is a
    # laptop, restarted every few minutes, by as many people as work on the agent. One agent log
    # per slug for every world, so a hundred developers wrote a hundred registrations a day into
    # the same log production's deploys are recorded in, until it could not be read. A deployment
    # still writes its own: when a slug started answering the telephone IS worth keeping.
    async def _append(
        self, slug: str, type: str, event: WireModel, env: Env | None = None
    ) -> Entry:
        """The claim is not accepted until the agent's own log says so; call is None, always."""
        forgettable = None if env is None or is_a_deployment(env) else True
        return await self._logs.writing_agent(slug).append(type, encode(event), forgettable)


# ── how a route asks for it ─────────────────────────────────────────────────────


def the_registry(connection: HTTPConnection) -> Registry:
    """Who owns which agent and which doors right now."""
    return held(connection, "registry", Registry)


RegistryDep = Annotated[Registry, Depends(the_registry)]
