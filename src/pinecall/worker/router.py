"""Who this job is for: what the dispatch said, then the number dialled, then the default."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, cast

from livekit import rtc
from livekit.protocol import agent as jobs

from pinecall._exceptions import PinecallError
from pinecall.session.voice import sip
from pinecall.types import ENVS, PRODUCTION, SANDBOX, THE_WIDGET, Channel, Direction, Env, Route
from pinecall.types.dispatch import (
    AGENT_KEY,
    APP_KEY,
    CALLER_KEY,
    DIRECTION_KEY,
    DIVERTED_KEY,
    ENV_KEY,
    HOLDER_KEY,
    ORG_KEY,
    RUN_KEY,
)

# A dispatch has already named the agent, so its seat is read only if somebody is on it already.
NOT_WAITED_FOR = 0.0


class NoRoute(PinecallError):
    """Nothing in the job says which agent this call is for, and the process has no default."""


# Whose call a dispatch says it is. The worker holds one key for every org, so this — and not the
# key — is what it asks the gateway's doors with: that org's routes, that org's declaration, that
# corner's copy in the sandbox. A dispatch that says nothing is the box's own trunk, or a room
# somebody made by hand, and the number dialled answers the question instead.
@dataclass(frozen=True)
class Whose:
    """The org, the world and the corner a dispatch named, each None when it did not."""

    org: str | None = None
    env: Env | None = None
    holder: str | None = None


@dataclass(frozen=True)
class Arrival:
    """What a job says about a call before anybody speaks: who called, and what they dialled."""

    caller: str
    channel: Channel
    direction: Direction
    agent: str | None = None
    number: str | None = None
    # Which app socket this dispatch asked for, when it named one. A spoken eval run does.
    app: str | None = None
    # Which eval run opened this call, when one did. A spoken golden's call starts mid-conversation.
    run: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict[str, Any])
    whose: Whose = field(default_factory=Whose)


# Read off the job alone, before the room is joined: the routes are asked for with these while the
# connect is still in flight, which is what keeps the caller from sitting through one more round
# trip (worker/entry.py). A world the dispatch spelled wrong is a dispatch nobody of ours wrote,
# and it reads as none rather than as a refusal — the number dialled, or the default, still stands.
def whose(job: jobs.Job) -> Whose:
    """Whose call this job is, as far as its dispatch metadata says."""
    said = _metadata(job.metadata)
    env = _text(said.get(ENV_KEY))
    return Whose(
        org=_text(said.get(ORG_KEY)),
        env=cast(Env, env) if env in ENVS else None,
        holder=_text(said.get(HOLDER_KEY)),
    )


# The two sources, in the order of certainty, and this is the only place they meet. The metadata
# first: an explicit dispatch named the agent on purpose and has nothing to wait for. Then the SIP
# seat in the room, because a phone call is a room job — livekit fills `job.participant` for a
# publisher job (agents/job.py:997 calls it `publisher`) and leaves it empty for ours, so the
# number dialled rides the caller's leg and nowhere else. The process default is `resolve`'s word.
async def arrival_of(job: jobs.Job, room: rtc.Room) -> Arrival:
    """What this job says about the call: the dispatch metadata, and the SIP seat in the room."""
    said = _metadata(job.metadata)
    agent = _text(said.get(AGENT_KEY))
    outbound = said.get(DIRECTION_KEY) == "outbound"
    leg = await sip.the_sip_leg(room, wait=NOT_WAITED_FOR if agent else sip.WAIT_FOR_THE_LEG_S)
    numbers = sip.the_numbers(leg.attributes if leg is not None else {})
    return Arrival(
        caller=numbers.caller or _text(said.get(CALLER_KEY)) or job.room.name,
        # A dialled call has no SIP seat to read yet — this job is what will place it — so the
        # channel is what the dispatch says it is. Reading it off the room would have made every
        # outbound call a web call, and then `_of_agent` would refuse an agent with no widget.
        channel="phone" if (outbound or numbers.dialled) else THE_WIDGET,
        direction="outbound" if outbound else "inbound",
        agent=agent,
        number=numbers.dialled,
        app=_text(said.get(APP_KEY)) or None,
        run=_text(said.get(RUN_KEY)) or None,
        metadata=said,
        whose=whose(job),
    )


# The order is the order of certainty: a dispatch named the agent on purpose, a number is a door
# somebody bought, and the default is the flag the process was started with — which is how
# `pinecall talk` reaches one agent on a laptop with no routes table at all.
def resolve(arrival: Arrival, routes: Sequence[Route], default: str | None = None) -> Route:
    """The one route this call is for. NoRoute when the job names nothing anybody answers."""
    if arrival.agent:
        return _of_agent(arrival.agent, arrival.channel, routes)
    if arrival.number:
        return _at_door(arrival.channel, arrival.number, routes)
    if default:
        return _of_agent(default, arrival.channel, routes)
    raise NoRoute("the job names no agent, no number was dialled, and the worker has no default")


# Strictly the channel the call arrived on: an agent with a widget and no number does not answer
# a phone call, and a log that said "web" about a phone call would be a lie nobody could unpick.
def _of_agent(agent: str, channel: Channel, routes: Sequence[Route]) -> Route:
    """The agent's own door on the channel this call arrived through."""
    for route in routes:
        if route.agent == agent and route.channel == channel:
            return route
    looked_in = sorted({f"{route.org}/{route.env}" for route in routes}) or ["no org at all"]
    raise NoRoute(
        f"agent {agent!r} answers no {channel} door this worker knows: it looked in "
        f"{', '.join(looked_in)}"
    )


def _at_door(channel: Channel, number: str, routes: Sequence[Route]) -> Route:
    """Who answers this number: one agent per door, so the first match is the only match."""
    door = (channel, number)
    for route in routes:
        if route.door == door:
            return route
    raise NoRoute(f"nobody answers {number} on {channel}")


def _metadata(said: str) -> Mapping[str, Any]:
    """A job's metadata is a string that is usually JSON; anything else is simply not a dispatch."""
    if not said:
        return {}
    try:
        read: Any = json.loads(said)
    except ValueError:
        return {}
    return cast("dict[str, Any]", read) if isinstance(read, dict) else {}


def _text(value: Any) -> str | None:
    """A metadata field the platform wrote, only when whoever wrote it wrote a string."""
    return value if isinstance(value, str) and value else None


# A phone call to a production number that no dispatch aimed anywhere: the one kind of call a
# developer's own phone can take off production (`pinecall line from`). A widget visit, an eval
# run, an outbound call and a sandbox number are already where they were sent.
def may_be_a_developers(arrival: Arrival, route: Route) -> bool:
    """Whether this call is a production ring the gateway should be asked about."""
    return (
        arrival.number is not None
        and arrival.agent is None
        and arrival.direction == "inbound"
        and route.channel == "phone"
        and route.env == PRODUCTION
    )


def diverted(arrival: Arrival, route: Route, developer: str | None) -> tuple[Arrival, Route]:
    """The call as it is built: in that developer's sandbox corner, or unchanged when nobody's."""
    if developer is None:
        return arrival, route
    whose = Whose(org=route.org, env=SANDBOX, holder=developer)
    marked = {**arrival.metadata, DIVERTED_KEY: PRODUCTION}
    return replace(arrival, whose=whose, metadata=marked), replace(route, env=SANDBOX)
