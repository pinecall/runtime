"""Who holds each agent: the app sockets registered in this process, per scope, and the line."""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from uuid import uuid4

from pinecall.domain.agent import AgentConfig
from pinecall.domain.errors import DeclarationRefused
from pinecall.domain.names import PRODUCTION, Env, JsonObject
from pinecall.domain.scope import THE_ORGS_OWN, Scope
from pinecall.log.logs import Logs
from pinecall.wire.events import (
    AgentConfigured,
    AgentDetached,
    AgentDraining,
    AgentRegistered,
)
from pinecall.wire.frames import Entry

# Minted, never id(websocket): CPython reuses addresses, and the id travels back as `?app=`.
type SocketId = str


# A slug is one org's: the scope names the org, the world and whose copy it is.
type Held = tuple[Scope, str]


AN_APP = "app_"


NOT_HOLDING = "agent {slug} is not held in {env} by an app of yours that answers an unclaimed call"


ANOTHER_ORGS = "agent {slug} belongs to another org: a slug is one org's"


NOT_REGISTERED = "agent {slug} is not registered on this socket: register it first"


type Stop = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class Registration:
    """One agent as one app socket holds it."""

    slug: str
    scope: Scope
    owner: SocketId
    config: AgentConfig
    sdk: str | None = None
    # False for a console: it takes only the calls that name it.
    takes_unclaimed: bool = True
    # The order of claims across scopes, so the newest holder is known.
    claimed: int = 0
    # Set by agent.drain: its tools still answer, it takes no new call.
    draining: bool = False

    @property
    def held_as(self) -> Held:
        """The registry's key for this agent in its scope."""
        return (self.scope, self.slug)


# Durable facts are in the log; this is which sockets are open now. The line is where a ring
# lands: the org's own scope in production, one developer's in the sandbox, which an org shares.
class Sockets:
    """The agents the app sockets hold, per scope, the line of each, and the developers' phones."""

    def __init__(self, logs: Logs) -> None:
        """Nobody holds anything yet."""
        self.logs = logs
        self.holders: dict[Held, list[Registration]] = {}
        self.lines: dict[tuple[Env, str], str] = {}
        self.phones: dict[tuple[Env, str], str] = {}
        self.owned: dict[SocketId, set[Held]] = {}
        self.claims = 0

    # ── reading ──

    def of(self, scope: Scope, slug: str) -> Registration | None:
        """The newest holder in this scope, else in the org's own."""
        return self._newest((scope, slug)) or self._newest((orgs_own(scope), slug))

    def serving(self, scope: Scope, slug: str, app: SocketId | None) -> Registration | None:
        """The socket a call opened in this scope goes to: the one named, else the newest taker."""
        if app is not None:
            found = self.on(app, scope.env, slug)
            return found if found is not None and found.scope.org == scope.org else None
        return self._taker((scope, slug)) or self._taker((orgs_own(scope), slug))

    # A caller's registered phone reaches their own scope first; then the line.
    def taking(self, scope: Scope, slug: str, caller: str | None) -> Registration | None:
        """The socket a ring at the agent lands in."""
        if caller is not None and (holder := self.phones.get((scope.env, caller))) is not None:
            theirs = self._taker((replace(scope, holder=holder), slug))
            if theirs is not None:
                return theirs
        holder = self.lines.get((scope.env, slug))
        if holder is None:
            return None
        return self._taker((replace(scope, holder=holder), slug))

    def on(self, app: SocketId, env: Env, slug: str) -> Registration | None:
        """The agent as this socket holds it in this world."""
        for found in self.owned.get(app, ()):
            if found[0].env == env and found[1] == slug:
                return next(
                    (item for item in self.holders.get(found, ()) if item.owner == app), None
                )
        return None

    def owned_by(self, app: SocketId) -> list[Registration]:
        """Every agent one socket holds, in claim order."""
        found = (item for name in self.owned.get(app, ()) for item in self.holders.get(name, ()))
        return sorted(
            (holder for holder in found if holder.owner == app),
            key=lambda item_found: item_found.claimed,
        )

    # One row per slug, the reader's scope over the org's; every scope for a team reader.
    def holding(self, scope: Scope, *, every_corner: bool) -> list[Registration]:
        """The org's held agents in the world, as this reader sees them."""
        seen: dict[str | Held, Registration] = {}
        for (owner, slug), holding in self.holders.items():
            if owner.org != scope.org or owner.env != scope.env or not holding:
                continue
            mine = owner.holder in (THE_ORGS_OWN, scope.holder)
            if not every_corner and not mine:
                continue
            under: str | Held = (owner, slug) if every_corner else slug
            if under not in seen or owner.holder == scope.holder:
                seen[under] = holding[-1]
        return list(seen.values())

    def slugs(self, org: str) -> frozenset[str]:
        """Every slug the org holds, in any world and scope: the agents quota counts these."""
        return frozenset(
            slug
            for (owner_scope, slug), kept in self.holders.items()
            if owner_scope.org == org and kept
        )

    # A log reader knows the slug alone: production's declaration, else any world's.
    def declared(self, slug: str) -> AgentConfig | None:
        """The agent's declared config, production's first."""
        found = sorted(
            (kept[-1] for (_, name), kept in self.holders.items() if name == slug and kept),
            key=lambda item_found: (item_found.scope.env != PRODUCTION, -item_found.claimed),
        )
        return found[0].config if found else None

    # ── the line ──

    def line_of(self, env: Env, slug: str) -> str | None:
        """The scope holding the agent's line; None when nobody does."""
        return self.lines.get((env, slug))

    # Taking the line is said out loud: starting later never takes a colleague's calls.
    def take_the_line(self, scope: Scope, slug: str) -> None:
        """Give the agent's line to this scope, which must hold an app that answers a ring."""
        if self._taker((scope, slug)) is None:
            raise DeclarationRefused(NOT_HOLDING.format(slug=slug, env=scope.env))
        self.lines[(scope.env, slug)] = scope.holder

    def drop_the_line(self, scope: Scope, slug: str) -> bool:
        """Let the line go, to the newest other scope that could take it."""
        if self.lines.get((scope.env, slug)) != scope.holder:
            return False
        del self.lines[(scope.env, slug)]
        self._next_takes_the_line(scope, slug, leaving=scope.holder)
        return True

    def waiting_for_the_line(self, scope: Scope, slug: str) -> list[Registration]:
        """Every scope of the org that could take the line, newest first."""
        taking = [
            item
            for (owner_scope, name) in self.holders
            if name == slug and owner_scope.org == scope.org and owner_scope.env == scope.env
            if (item := self._taker((owner_scope, name))) is not None
        ]
        return sorted(taking, key=lambda item: item.claimed, reverse=True)

    # ── the developers' phones ──

    def calls_from(self, env: Env, number: str, holder: str) -> None:
        """Send rings from this phone to this person's scope; the last one said wins."""
        self.phones[(env, number)] = holder

    def forget_calls_from(self, env: Env, holder: str) -> list[str]:
        """Forget every phone of this person, and say which."""
        gone = sorted(
            n
            for (world, n), owner_scope in self.phones.items()
            if world == env and owner_scope == holder
        )
        for number in gone:
            del self.phones[(env, number)]
        return gone

    def calling(self, env: Env, holder: str) -> list[str]:
        """The phones this person rings from."""
        return sorted(
            n
            for (world, n), owner_scope in self.phones.items()
            if world == env and owner_scope == holder
        )

    # ── claiming ──

    async def register(
        self, owner: SocketId, scope: Scope, slug: str, *, sdk: str | None, takes_unclaimed: bool
    ) -> Entry:
        """This socket holds the agent from now on; agent.registered is written on its log."""
        store = self.logs.store
        owner_org = await store.owner(slug)
        if owner_org is not None and owner_org != scope.org:
            raise DeclarationRefused(ANOTHER_ORGS.format(slug=slug))
        await store.claim(None, slug, scope.org)
        # A call that arrives before agent.configure still runs on what the agent declared.
        registration = self.on(owner, scope.env, slug) or self.of(scope, slug)
        config = registration.config if registration is not None else AgentConfig(slug=slug)
        self._replace(
            Registration(
                slug=slug,
                scope=scope,
                owner=owner,
                config=config,
                sdk=sdk,
                takes_unclaimed=takes_unclaimed,
            )
        )
        data = AgentRegistered(routes=[], app=owner, sdk=sdk, env=scope.env)
        return await self._written(scope.env, slug, "agent.registered", data.written())

    async def configure(
        self, owner: SocketId, env: Env, slug: str, config: AgentConfig, changed: Sequence[str]
    ) -> Entry:
        """The declaration this socket sent; agent.configured names what changed."""
        found = self.on(owner, env, slug)
        if found is None:
            raise DeclarationRefused(NOT_REGISTERED.format(slug=slug))
        self._replace(replace(found, config=config))
        data = AgentConfigured(changed=list(changed))
        return await self._written(env, slug, "agent.configured", data.written())

    # A deploy: the socket keeps the agent so its tools still answer, and takes no new call.
    def drain(self, owner: SocketId, env: Env, slug: str) -> None:
        """Mark the socket as draining the agent."""
        found = self.on(owner, env, slug)
        if found is None:
            raise DeclarationRefused(NOT_REGISTERED.format(slug=slug))
        self._replace(replace(found, draining=True))

    async def drained(
        self, owner: SocketId, env: Env, slug: str, *, handed: int, parked: int
    ) -> Entry:
        """agent.draining, once the socket's calls moved."""
        data = AgentDraining(app=owner, env=env, handed=handed, parked=parked)
        return await self._written(env, slug, "agent.draining", data.written())

    async def release(self, owner: SocketId) -> None:
        """A socket closed: its agents let go, the line handed on, agent.detached for each."""
        for found in self.owned.pop(owner, set()):
            left = [item for item in self.holders.get(found, ()) if item.owner != owner]
            if left:
                self.holders[found] = left
            else:
                self.holders.pop(found, None)
            scope, slug = found
            if self.lines.get((scope.env, slug)) == scope.holder and not left:
                del self.lines[(scope.env, slug)]
                self._next_takes_the_line(scope, slug)
            data = AgentDetached(app=owner, env=scope.env, left=not left)
            await self._written(scope.env, slug, "agent.detached", data.written())

    def _replace(self, registration: Registration) -> None:
        self.claims += 1
        claim = replace(registration, claimed=self.claims)
        holding = self.holders.setdefault(claim.held_as, [])
        at = next(
            (n for n, item_found in enumerate(holding) if item_found.owner == claim.owner), None
        )
        if at is None:
            holding.append(claim)
        else:
            holding[at] = claim
        # The first scope able to take a ring gets the line; later ones claim it.
        if claim.takes_unclaimed and not claim.draining:
            self.lines.setdefault((claim.scope.env, claim.slug), claim.scope.holder)
        self.owned.setdefault(claim.owner, set()).add(claim.held_as)

    def _newest(self, holding_found: Held) -> Registration | None:
        holding = self.holders.get(holding_found)
        return holding[-1] if holding else None

    def _taker(self, holding_found: Held) -> Registration | None:
        holding = reversed(self.holders.get(holding_found, ()))
        return next((item for item in holding if item.takes_unclaimed and not item.draining), None)

    def _next_takes_the_line(self, scope: Scope, slug: str, *, leaving: str | None = None) -> None:
        waiting = [
            item for item in self.waiting_for_the_line(scope, slug) if item.scope.holder != leaving
        ]
        if waiting:
            self.lines[(scope.env, slug)] = waiting[0].scope.holder

    # A laptop restarts often: in the sandbox these entries are forgettable, or they flood the log.
    async def _written(self, env: Env, slug: str, kind: str, data: JsonObject) -> Entry:
        forgettable = None if env == PRODUCTION else True
        return await self.logs.agent(slug).append(kind, data, ephemeral=forgettable)


@dataclass(frozen=True)
class Process:
    """One connected app socket, as the org's list of processes shows it."""

    app: SocketId
    scope: Scope
    address: str | None
    connected_at: float
    # Sends `error stopped` and closes: the SDK exits instead of reconnecting.
    stop: Stop
    host: str | None = None


def orgs_own(scope: Scope) -> Scope:
    return replace(scope, holder=THE_ORGS_OWN)


def new_socket_id() -> SocketId:
    """A new id for an app socket."""
    return f"{AN_APP}{uuid4().hex[:12]}"
