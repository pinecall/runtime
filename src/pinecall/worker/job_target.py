"""Who this job is for: what the dispatch said, then the number dialled, then the default."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

from livekit import api, rtc
from livekit.protocol import agent as jobs

from pinecall._exceptions import PinecallError
from pinecall.session.voice import sip
from pinecall.types import ENVS, PRODUCTION, SANDBOX, THE_WIDGET, Channel, Direction, Env, Route
from pinecall.types.dispatch import (
    ACCEPTS_KEY,
    AGENT_KEY,
    APP_KEY,
    CALLER_KEY,
    DECLINES_KEY,
    DIRECTION_KEY,
    DIVERTED_KEY,
    ENV_KEY,
    HOLDER_KEY,
    ORG_KEY,
    PERSONA_KEY,
    RUN_KEY,
    Handover,
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
    # Which synthetic caller a model is playing on it, when a spoken simulation named one — and
    # that caller's own rule for the call, when it wrote one, for the judge at hang-up.
    persona: str | None = None
    accepts_when: str | None = None
    declines_when: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict[str, Any])
    whose: Whose = field(default_factory=Whose)


# Read off the job alone, before the room is joined: the routes are asked for with these while the
# connect is still in flight, which is what keeps the caller from sitting through one more round
# trip (worker/job.py). A world the dispatch spelled wrong is a dispatch nobody of ours wrote,
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
    leg = await sip.wait_for_sip_leg(room, wait=NOT_WAITED_FOR if agent else sip.WAIT_FOR_THE_LEG_S)
    numbers = sip.sip_numbers(leg.attributes if leg is not None else {})
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
        persona=_text(said.get(PERSONA_KEY)) or None,
        accepts_when=_text(said.get(ACCEPTS_KEY)) or None,
        declines_when=_text(said.get(DECLINES_KEY)) or None,
        metadata=said,
        whose=whose(job),
    )


# The order is the order of certainty: a dispatch named the agent on purpose, a number is a door
# somebody bought, and the default is the flag the process was started with — which is how
# `pinecall talk` reaches one agent on a laptop with no routes table at all.
def resolve(arrival: Arrival, routes: Sequence[Route], default: str | None = None) -> Route:
    """The one route this call is for. NoRoute when the job names nothing anybody answers."""
    if arrival.agent:
        return _of_agent(arrival.agent, arrival, routes)
    if arrival.number:
        return _at_door(arrival.channel, arrival.number, routes)
    if default:
        return _of_agent(default, arrival, routes)
    raise NoRoute("the job names no agent, no number was dialled, and the worker has no default")


# There is no web door in any table and there never was one an operator could type: a number is a
# row somebody bought and the widget is not. Every agent is on the web, so the route a widget call
# runs on is made here out of what the dispatch already carries — the same route the chat socket
# mints for a written visit (api/calls/chat.py). The gateway has already said whose agent it is:
# the token door refuses one this key does not hold, and this job exists because it did not.
def _the_widgets_own(slug: str, whose: Whose) -> Route | None:
    """The route a browser's call runs on: this agent, in the corner the dispatch named."""
    if not whose.org or whose.env is None:
        return None
    return Route(org=whose.org, agent=slug, channel=THE_WIDGET, number=None, env=whose.env)


# A production ring handed to a developer's corner (`handing_over` below) rang the REAL door: the
# number is production's row, in production's database, and no table of the instance building it
# holds it — nor should a sandbox number of the same agent stand in for it, which is why this is
# asked before the rows are. The route keeps the number dialled, in the corner the dispatch named.
def _handed_over(slug: str, arrival: Arrival) -> Route | None:
    """The route a handed-over ring runs on: the number it rang, in the developer's corner."""
    whose = arrival.whose
    if not arrival.metadata.get(DIVERTED_KEY) or arrival.number is None:
        return None
    if not whose.org or whose.env is None:
        return None
    return Route(
        org=whose.org, agent=slug, channel=arrival.channel, number=arrival.number, env=whose.env
    )


# Strictly the channel the call arrived on: an agent with a widget and no number does not answer
# a phone call, and a log that said "web" about a phone call would be a lie nobody could unpick.
def _of_agent(agent: str, arrival: Arrival, routes: Sequence[Route]) -> Route:
    """The agent's own door on the channel this call arrived through."""
    if (handed := _handed_over(agent, arrival)) is not None:
        return handed
    for route in routes:
        if route.agent == agent and route.channel == arrival.channel:
            return route
    if arrival.channel == THE_WIDGET and (its_own := _the_widgets_own(agent, arrival.whose)):
        return its_own
    looked_in = sorted({f"{route.org}/{route.env}" for route in routes}) or ["no org at all"]
    raise NoRoute(
        f"agent {agent!r} answers no {arrival.channel} door this worker knows: it looked in "
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


# The room is handed over, not the call rebuilt: the caller is already in it, the SIP trunk and
# rule stay production's, and a job for the other fleet is dispatched into the SAME room — which is
# how the call reaches the instance that holds the developer's corner, its gateway, its database
# and its log. The metadata is what any dispatch of ours says, so that fleet's router reads it like
# every other: whose (the org, the sandbox, the developer), which agent, who is calling — and where
# it rang, which the log keeps.
def handing_over(
    room: str, arrival: Arrival, route: Route, handover: Handover
) -> api.CreateAgentDispatchRequest:
    """The dispatch that hands this production ring to the developer's corner, in the same room."""
    said = {
        ORG_KEY: route.org,
        ENV_KEY: SANDBOX,
        HOLDER_KEY: handover.holder,
        AGENT_KEY: route.agent,
        CALLER_KEY: arrival.caller,
        DIRECTION_KEY: arrival.direction,
        DIVERTED_KEY: PRODUCTION,
    }
    return api.CreateAgentDispatchRequest(
        room=room, agent_name=handover.fleet, metadata=json.dumps(said, separators=(",", ":"))
    )
