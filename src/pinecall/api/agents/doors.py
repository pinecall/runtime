"""The doors somebody dials: which agent answers at each, and whose terminal its ring goes to."""

from __future__ import annotations

from collections.abc import Sequence

from pinecall.providers import declaration
from pinecall.types import DeclarationRefused, Env, Route

# One world and one slug: what a dialled door answers for, and what a listing shows once.
type Agent = tuple[Env, str]

# What the public reaches: the channel, and the number when the channel has one.
type Door = tuple[str, str | None]


class Doors:
    """Every dialled door this process answers, and which corner of a world picks each ring up.

    Two tables with one subject. `_at` is the public side: a number exists once in a world and
    answers for one agent, whoever is holding it — which is why it is keyed by the world and the
    slug and by nobody's corner.

    `_line` is who picks up. Nobody's corner in production, where what is deployed is the only
    holder there is. In development the developer who has the LINE: an org shares one development
    number, a shared number rings in one terminal, and which terminal is claimed out loud instead
    of being whoever restarted last — a call landing in a colleague's scrollback is a call nobody
    notices they took. One developer alone claims nothing: the first to hold an agent gets its
    line, and it is handed on when they leave.
    """

    def __init__(self) -> None:
        self._at: dict[Door, Agent] = {}
        self._line: dict[Agent, str | None] = {}
        self._calling: dict[tuple[Env, str], str] = {}

    # ── the public side ─────────────────────────────────────────────────────────

    def at(self, door: Door) -> Agent | None:
        """Which agent answers here, in whichever world claimed it. None when nobody does."""
        return self._at.get(door)

    # The web route is deliberately left out: see docs/decisions/routes.md. Every agent's widget
    # would be the one door ("web", None), and the second agent of a fleet would be refused it.
    def answered_by(self, agent: Agent, routes: Sequence[Route]) -> None:
        """These are the doors this agent answers now, and none of the ones it used to."""
        wanted = {route.door for route in declaration.dialled(routes)}
        for door in [door for door, answered in self._at.items() if answered == agent]:
            if door not in wanted:
                del self._at[door]
        for door in wanted:
            self._at[door] = agent

    def refuse_a_taken_one(self, agent: Agent, routes: Sequence[Route]) -> None:
        """A number answers for one agent in one world at a time, and never twice in a register."""
        _, slug = agent
        claimed: set[Door] = set()
        for route in declaration.dialled(routes):
            if route.door in claimed:
                raise DeclarationRefused(f"agent {slug} claims the door {said(route)} twice")
            claimed.add(route.door)
            taken = self._at.get(route.door)
            if taken is not None and taken != agent:
                taken_env, taken_slug = taken
                raise DeclarationRefused(
                    f"the door {said(route)} already answers for agent {taken_slug} in {taken_env}"
                )

    # ── whose terminal it rings in ──────────────────────────────────────────────

    def claimed(self, agent: Agent) -> bool:
        """Whether any corner is holding this agent's line at all."""
        return agent in self._line

    def line(self, agent: Agent) -> str | None:
        """Whose corner the ring goes to. Nobody's corner is None too, so ask `claimed` first."""
        return self._line.get(agent)

    def take(self, agent: Agent, holder: str | None) -> None:
        """Hand the ring to this corner, whoever had it. The claim a second developer makes."""
        self._line[agent] = holder

    def take_if_free(self, agent: Agent, holder: str | None) -> None:
        """The first corner to hold an agent answers its ring: alone, nobody claims anything."""
        if agent not in self._line:
            self._line[agent] = holder

    def release(self, agent: Agent, holder: str | None) -> bool:
        """This corner stops answering the ring. False when the line was not theirs to release."""
        if agent not in self._line or self._line[agent] != holder:
            return False
        del self._line[agent]
        return True

    # ── whose phone dialled ─────────────────────────────────────────────────────

    # The line answers "and if nobody knows who this is". THIS answers the question before it:
    # a developer says which number they call FROM, and every call they make to a development
    # door lands in their own corner — no claim, no coordination, and three of them testing at
    # once. Kept here beside the live table and not in a row, because it is only ever meaningful
    # alongside a socket: a developer who is running nothing has no corner to route a call into.
    def whose_call(self, env: Env, caller: str) -> str | None:
        """The corner that said it calls from this number, or None when nobody did."""
        return self._calling.get((env, caller))

    def calls_from(self, env: Env, caller: str, holder: str) -> None:
        """This corner answers what it dials itself. A number is one person's: the last wins."""
        self._calling[(env, caller)] = holder

    def forget_calls_from(self, env: Env, holder: str) -> tuple[str, ...]:
        """Every number this corner had claimed, forgotten. What it was holding, for the answer."""
        gone = tuple(
            number
            for (world, number), whose in self._calling.items()
            if world == env and whose == holder
        )
        for number in gone:
            del self._calling[(env, number)]
        return gone

    def calling(self, env: Env, holder: str | None) -> tuple[str, ...]:
        """The numbers whose calls reach this corner, sorted. Empty for a corner that named none."""
        if holder is None:
            return ()
        return tuple(
            sorted(
                number
                for (world, number), whose in self._calling.items()
                if world == env and whose == holder
            )
        )


def said(route: Route) -> str:
    """A door, for a person: 'phone +34910000000'. Only a dialled door is ever refused."""
    return f"{route.channel} {route.number}"
