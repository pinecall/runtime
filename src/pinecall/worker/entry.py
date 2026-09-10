"""One job, from the room to the sealed log: who it is for, what answers it, what closes it."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

from livekit.agents import NOT_GIVEN, JobContext, NotGivenOr
from livekit.agents.voice import Agent, AgentSession
from livekit.agents.voice.room_io import RoomOptions

from pinecall.session import clock, greeting
from pinecall.session.voice import session
from pinecall.session.voice.kit import Kit
from pinecall.session.voice.platform import Platform
from pinecall.types import AgentConfig, CallContext, Route
from pinecall.worker import commanding, recordings, router, seat
from pinecall.worker.client import Gateway
from pinecall.worker.recordings import Keeping
from pinecall_protocol import Command

logger = logging.getLogger(__name__)


class Bridge(Protocol):
    """What the worker needs of the bridge: the agent livekit runs, and both ends of the log."""

    @property
    def agent(self) -> Agent:
        """The livekit Agent of this call: our prompt's blocks, our tools, our ears."""
        ...

    async def opened(self, live: AgentSession[None]) -> None:
        """The session is built and about to start: subscribe to it, and write call.started."""
        ...

    async def apply(self, command: Command) -> None:
        """One protocol command from the app onto this call."""
        ...

    async def closed(self, reason: str) -> None:
        """The job is shutting down: call.ended, then call.summary with the usage and the cost."""
        ...


type Bridging = Callable[[CallContext, AgentConfig, Platform, Path | None], Bridge]
"""How one call gets its bridge, told where its audio will be. What a bridge IS is the bridge's."""


@dataclass(frozen=True)
class Worker:
    """What one process holds and every job of it shares: the gateway, the vendors, the bridge."""

    gateway: Gateway
    kit: Kit
    bridging: Bridging
    keeping: Keeping
    # The agent a job that names none is for: the flag `pinecall talk` starts a laptop worker with.
    default_agent: str | None = None
    # The app socket every call of this process claims, when it was started by one — `pinecall
    # talk` names its own. Unset on a box, where a call takes the newest socket holding the agent.
    app: str | None = None


async def answer(ctx: JobContext, worker: Worker) -> None:
    """One job: join the room, resolve who it is for, run the session, and seal its log after."""
    began = time.monotonic()
    # Every livekit log line of this process from here on names the call it belongs to; a box
    # running forty at once has no other way to read its own log (basic_agent.py:73-76).
    ctx.log_context_fields = {"room": ctx.job.room.name}
    # The room comes first: a phone call is a room job, so the number that was dialled is on the
    # caller's SIP seat in the room and there is nothing to route by until we are in it. The
    # routes table is not: it belongs to the fleet, not to this call, so it is asked for WHILE
    # the room is being joined instead of after — see docs/decisions/worker.md.
    _, routes = await asyncio.gather(ctx.connect(), worker.gateway.routes())
    arrival = await router.arrival_of(ctx.job, ctx.room)
    route = router.resolve(arrival, routes, worker.default_agent)
    # Two doors, one wait: whose keys this call runs on is a second question about the same agent,
    # and asking it in parallel with the config costs the caller nothing. See providers/registry.py.
    config, keys = await asyncio.gather(
        worker.gateway.agent(route.agent), worker.gateway.provider_keys(route.agent)
    )
    context = a_call(ctx.room.name or ctx.job.id, arrival, route)
    # What the dispatch named wins over the flag this process was started with: a spoken eval
    # run has to reach the terminal holding its goldens, and that socket takes no unclaimed call.
    await worker.gateway.opened(context, route.agent, arrival.app or worker.app)
    recording = where_the_audio_goes(ctx, context.call, worker.keeping)
    bridge = worker.bridging(context, config, worker.gateway, recording)
    # Registered before anything can fail: a call that dies mid-setup still seals its own log.
    ctx.add_shutdown_callback(sealing(worker.gateway, bridge, context.call))
    live = session.a_session(config, worker.kit, route.channel, keys)
    await clock.seeded(bridge.agent, context.today)
    await bridge.opened(live)
    # The one voice this session answers, decided before it subscribes to anything: a listener and,
    # a supervisor sit in the same room and the agent must never transcribe either.
    # Nobody seated yet leaves the identity unset, which is livekit's own first-comer rule.
    pinned = await seat.the_callers_seat(ctx.room, route.channel)
    # Said out loud either way: the default defers to the server, and it is only safe today because
    # a self-hosted LiveKit is not a cloud host — see docs/decisions/livekit-session.md §7.
    await live.start(  # pyright: ignore[reportUnknownMemberType]
        bridge.agent,
        room=ctx.room,
        room_options=RoomOptions(participant_identity=pinned or NOT_GIVEN),
        record=recordings.AUDIO_ONLY if recording is not None else False,
    )
    logger.info("the pipeline is live %.2fs after the job arrived", time.monotonic() - began)
    # The opening, before the queue is served, because it is the FIRST thing said: a class that
    # declared a greeting speaks now, and whatever the app sent while the room was being joined
    # arrives after it. Its turn is a turn.agent like any other; nothing here is special-cased.
    await greeting.open_the_call(config.greeting, say=_saying(live), reply=_replying(live))
    # Last: an `agent.say` has a started session to say it on. Everything the app sent before this
    # waits in the gateway's queue and arrives in the order it was sent.
    commands = asyncio.ensure_future(commanding.served(worker.gateway, bridge, context.call))
    ctx.add_shutdown_callback(letting_go(commands))


# The same two calls session/voice/commands.py makes for agent.say and agent.reply, handed to the
# greeting as a pair. livekit's own default for allow_interruptions is NOT_GIVEN, and a greeting
# that says nothing about it must reach the session as undeclared rather than as a guess.
def _saying(live: AgentSession[None]) -> greeting.Speaks:
    """The verbatim verb: the words, out loud, with no model in the loop."""

    async def said(text: str, interruptible: bool | None) -> None:
        live.say(text, allow_interruptions=_or_livekits(interruptible))

    return said


def _replying(live: AgentSession[None]) -> greeting.Speaks:
    """The improvised verb: one model turn, guided by words the caller never hears."""

    async def replied(instructions: str, interruptible: bool | None) -> None:
        live.generate_reply(
            instructions=instructions, allow_interruptions=_or_livekits(interruptible)
        )

    return replied


def _or_livekits(interruptible: bool | None) -> NotGivenOr[bool]:
    """Undeclared is not False: an absent flag leaves the session's own default in place."""
    return NOT_GIVEN if interruptible is None else interruptible


# Decided before the session exists, so the bridge is born knowing the pointer call.summary will
# carry and the session is told to record exactly what the log points at — and nothing when the
# box keeps no audio (RECORD=0), when the directory is never composed at all.
def where_the_audio_goes(ctx: JobContext, call: str, keeping: Keeping) -> Path | None:
    """The file this call's audio will be in, or None when none is kept."""
    destination = keeping(call)
    if destination is None:
        return None
    return recordings.kept_by_the_job(ctx, destination)


# The room's name IS the call id: a reader of the log can find the room and the room can find the
# log, with nothing minted in between and nothing to keep in step.
def a_call(call: str, arrival: router.Arrival, route: Route) -> CallContext:
    """The call as the platform will know it, before the first word is spoken."""
    return CallContext(
        call=call,
        channel=route.channel,
        direction=arrival.direction,
        caller=arrival.caller,
        route=route,
        today=date.today(),
        metadata=arrival.metadata,
    )


def letting_go(reading: asyncio.Task[None]) -> Callable[[str], Coroutine[None, None, None]]:
    """The shutdown callback that closes the command stream: the call is over."""

    async def stop(reason: str) -> None:  # noqa: ARG001 — livekit hands every callback the reason
        reading.cancel()

    return stop


def sealing(
    gateway: Gateway, bridge: Bridge, call: str
) -> Callable[[str], Coroutine[None, None, None]]:
    """The shutdown callback: the bridge says how the call ended, then the log is closed."""

    async def seal(reason: str) -> None:
        await bridge.closed(reason)
        await gateway.sealed(call)

    return seal
