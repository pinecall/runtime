"""One agent as ONE socket holds it: whose corner it is, which doors it took, what it declared."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import uuid4

from pinecall.api.agents.doors import Agent
from pinecall.log.entry import Entry
from pinecall.types import AgentConfig, Env, Route

# A socket's id is minted, not id(websocket): it travels to the app in agent.registered and comes
# back on `?app=`, and CPython reuses an address the moment the object at it is collected — a
# stale one would name a socket somebody else now holds.
type SocketId = str

_AN_APP = "app_"


def a_socket_id() -> SocketId:
    """One connected app, told apart from every other for as long as this process runs."""
    return f"{_AN_APP}{uuid4().hex[:12]}"


# How an entry reaches somebody who is reading a call.
type Send = Callable[[Entry], Awaitable[None]]

# The name an agent is held under: its world, whose corner of that world, and its slug. Nobody's
# corner in production — what is deployed is the ORG's, held by the key its box runs on, and a
# person's key does not open `app` there at all (types/key.py). In the sandbox the member the key
# was minted for, so two developers of one tenant each hold their own `tienda-sur` and neither
# takes the other's; a sandbox key that names nobody — CI's — holds the org's own, which is
# what a developer holding nothing falls back to. A dialled door is namespaced by none of it:
# api/agents/doors.py, and docs/decisions/dispatch.md.
type Held = tuple[Env, str | None, str]


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
    # Whose corner of `env` this is: the member in the sandbox, nobody in production and nobody
    # for a sandbox key that names none. See `Held` above. Last with the defaulted fields
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
