"""Who this job is for: what the dispatch said, then the number dialled, then the default."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

from livekit import rtc
from livekit.protocol import agent as jobs

from pinecall._exceptions import PinecallError
from pinecall.session.voice import sip
from pinecall.types import THE_WIDGET, Channel, Direction, Route
from pinecall.types.dispatch import AGENT_KEY, APP_KEY, CALLER_KEY, DIRECTION_KEY, RUN_KEY

# A dispatch has already named the agent, so its seat is read only if somebody is on it already.
NOT_WAITED_FOR = 0.0


class NoRoute(PinecallError):
    """Nothing in the job says which agent this call is for, and the process has no default."""


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


# The two sources, in the order of certainty, and this is the only place they meet. The metadata
# first: an explicit dispatch named the agent on purpose and has nothing to wait for. Then the SIP
# seat in the room, because a phone call is a room job — livekit fills `job.participant` for a
# publisher job (agents/job.py:997 calls it `publisher`) and leaves it empty for ours, so the
# number dialled rides the caller's leg and nowhere else. The process default is `resolve`'s word.
async def arrival_of(job: jobs.Job, room: rtc.Room) -> Arrival:
    """What this job says about the call: the dispatch metadata, and the SIP seat in the room."""
    said = _metadata(job.metadata)
    agent = _text(said.get(AGENT_KEY))
    leg = await sip.the_sip_leg(room, wait=NOT_WAITED_FOR if agent else sip.WAIT_FOR_THE_LEG_S)
    numbers = sip.the_numbers(leg.attributes if leg is not None else {})
    return Arrival(
        caller=numbers.caller or _text(said.get(CALLER_KEY)) or job.room.name,
        channel="phone" if numbers.dialled else THE_WIDGET,
        direction="outbound" if said.get(DIRECTION_KEY) == "outbound" else "inbound",
        agent=agent,
        number=numbers.dialled,
        app=_text(said.get(APP_KEY)) or None,
        run=_text(said.get(RUN_KEY)) or None,
        metadata=said,
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
    raise NoRoute(f"agent {agent!r} answers no {channel} door this worker knows")


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
