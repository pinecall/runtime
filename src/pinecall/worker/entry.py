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
from pinecall.types.dispatch import DIAL_KEY, SCOPE_KEY, WRITTEN_SCOPE
from pinecall.worker import commanding, dialling, egress, recordings, router, seat
from pinecall.worker.client import Gateway
from pinecall.worker.egress import Stopping
from pinecall.worker.hold import the_melody
from pinecall.worker.recordings import Keeping
from pinecall_protocol import Command, defs, encode
from pinecall_protocol.events import CallEnded

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

    async def holding(self, melody: Path | None) -> None:
        """The room is live: what plays into it while a tool runs, or None for nothing."""
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
    # The start-up, step by step: a caller hears nothing until the pipeline is live, and a line
    # that said only "5.59s" left the seconds nowhere to be found (2026-09-16). Each awaited step
    # is timed under its own name and the live line carries the breakdown.
    steps: dict[str, float] = {}
    last = [began]

    def took(name: str) -> None:
        now = time.monotonic()
        steps[name] = round(now - last[0], 2)
        last[0] = now

    # Every livekit log line of this process from here on names the call it belongs to; a box
    # running forty at once has no other way to read its own log (basic_agent.py:73-76).
    ctx.log_context_fields = {"room": ctx.job.room.name}
    # Whose call this is comes off the dispatch alone, so the doors of THAT org — the worker holds
    # one key for every org — are asked for WHILE the room is being joined: a phone call is a room
    # job, and the number dialled is on the caller's SIP seat, so there is nothing else to route
    # by until we are in it. See docs/decisions/worker.md.
    whose = router.whose(ctx.job)
    _, routes = await asyncio.gather(
        ctx.connect(),
        worker.gateway.routes(org=whose.org, env=whose.env, holder=whose.holder),
    )
    took("room+routes")
    arrival = await router.arrival_of(ctx.job, ctx.room)
    took("arrival")
    # A call on the box's own trunk names no org: the number dialled is one org's door in one
    # world, and the gateway finds it across every org. One more round trip, on that path alone.
    if arrival.number is not None and whose.org is None:
        routes = await worker.gateway.routes(number=arrival.number, channel=arrival.channel)
    route = router.resolve(arrival, routes, worker.default_agent)
    # A developer testing on the real number: their own phone, dialling a production door while
    # they hold the agent in the sandbox, is built in their copy. Everybody else is unchanged.
    if router.may_be_a_developers(arrival, route):
        developer = await _a_developers(worker.gateway, route, arrival.caller)
        arrival, route = router.diverted(arrival, route, developer)
        whose = arrival.whose
        took("developer")
    # Two doors, one wait: whose keys this call runs on is a second question about the same agent,
    # and asking it in parallel with the config costs the caller nothing. See providers/registry.py.
    # Both are asked for the route's org and world — the one the call is for — and the corner the
    # dispatch named, so a sandbox call is built from the developer's own declaration.
    config, keys, melody = await asyncio.gather(
        worker.gateway.agent(route.agent, org=route.org, env=route.env, holder=whose.holder),
        worker.gateway.provider_keys(
            route.agent, org=route.org, env=route.env, holder=whose.holder
        ),
        the_melody(worker.gateway, route.agent, org=route.org, env=route.env, holder=whose.holder),
    )
    took("config+keys")
    context = a_call(ctx.room.name or ctx.job.id, arrival, route)
    # What the dispatch named wins over the flag this process was started with: a spoken eval
    # run has to reach the terminal holding its goldens, and that socket takes no unclaimed call.
    await worker.gateway.opened(context, route.agent, arrival.app or worker.app)
    took("opened")
    # A call this box PLACED is dialled here, by the job that will answer on it, and before there
    # is a session to say anything into an empty room. It waits for the far end to pick up, which
    # is the only way busy and no-answer are knowable at all; a call nobody answered ends right
    # here, on its own log, and this job is over. See docs/protocol/numbers.md.
    if not await _the_far_end_answered(ctx, worker, context, arrival):
        return
    # A `chat` visit is written: the session has no ears and no voice, and the room carries no
    # audio either way, so the words reach the page at the pace the model writes them.
    typed = arrival.metadata.get(SCOPE_KEY) == WRITTEN_SCOPE
    # Where this call's audio would go. A written call keeps none — a pointer to an audio.ogg
    # nobody wrote was a session screen that said the file was on another box — and whether a
    # spoken one is kept at all is the agent's own setting now, resolved with the rest of its
    # world (`pinecall agent set --record`).
    wanted = (
        None
        if typed or not config.record
        else where_the_audio_goes(ctx, context.call, worker.keeping)
    )
    # Asked for HERE, before the session is built and well before the greeting is spoken: the room
    # has been joined since ctx.connect() and the box's recorder takes a moment to come up, and
    # that moment is the one the session spends being built.
    taping = await _the_box_records(ctx, wanted)
    # A recorder that would not take the job is a call with no audio and a call all the same: the
    # summary points at nothing rather than at a file nobody is writing. Which is what the
    # doctor's `egress` line is for — nothing else would say it out loud.
    recording = wanted if taping is not None or recordings.the_session_records_itself() else None
    bridge = worker.bridging(context, config, worker.gateway, recording)
    # Registered before anything can fail: a call that dies mid-setup still seals its own log.
    ctx.add_shutdown_callback(sealing(worker.gateway, bridge, context.call, taping))
    live = session.a_session(config, worker.kit, route.channel, keys, spoken=not typed)
    took("session")
    await clock.seeded(bridge.agent, context.today)
    await bridge.opened(live)
    took("bridge")
    # The one voice this session answers, decided before it subscribes to anything: a listener and,
    # a supervisor sit in the same room and the agent must never transcribe either.
    # Nobody seated yet leaves the identity unset, which is livekit's own first-comer rule.
    pinned = await seat.the_callers_seat(ctx.room, route.channel, spoken=not typed)
    took("seat")
    # Said out loud either way: the default defers to the server, and it is only safe today because
    # a self-hosted LiveKit is not a cloud host — see docs/decisions/livekit-session.md §7.
    await live.start(  # pyright: ignore[reportUnknownMemberType]
        bridge.agent,
        room=ctx.room,
        room_options=RoomOptions(
            participant_identity=pinned or NOT_GIVEN,
            audio_input=False if typed else NOT_GIVEN,
            audio_output=False if typed else NOT_GIVEN,
        ),
        record=recordings.asked_of_the_session(recording),
    )
    took("start")
    # The hold melody's track, once the room is live. A written visit has no audio to play it in.
    await bridge.holding(None if typed else melody)
    logger.info(
        "the pipeline is live %.2fs after the job arrived",
        time.monotonic() - began,
        extra={"steps": steps},
    )
    # The opening, before the queue is served, because it is the FIRST thing said: a class that
    # declared a greeting speaks now, and whatever the app sent while the room was being joined
    # arrives after it. Its turn is a turn.agent like any other; nothing here is special-cased.
    await greeting.open_the_call(
        greeting.the_greeting_for(config.greeting, context.run),
        say=_saying(live),
        reply=_replying(live),
    )
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
# carry, and the directory is composed only for a call that is going to fill it.
def where_the_audio_goes(ctx: JobContext, call: str, keeping: Keeping) -> Path | None:
    """The file this call's audio will be in, or None when none is kept."""
    return recordings.kept_by_the_job(ctx, keeping(call))


# The box's own recorder, one room composite for this room: everything anybody on the call heard,
# the hold melody and a supervisor's voice with it. Under livekit's console there is no room on a
# server to compose and the session records itself instead, so nothing is asked for.
async def _the_box_records(ctx: JobContext, audio: Path | None) -> Stopping | None:
    """Ask the box to record this room, and answer with how to stop it and wait for its file."""
    if audio is None or recordings.the_session_records_itself():
        return None
    taping = await egress.recording_the_room(ctx.api, ctx.room.name, audio)
    if taping is None:
        return None

    async def stop() -> None:
        await egress.and_the_file_is_written(ctx.api, taping, audio)

    return stop


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
        run=arrival.run,
        persona=arrival.persona,
        holder=arrival.whose.holder,
    )


# True when there is somebody on the line to talk to: an inbound call always, and an outbound one
# once the far end picked up. The log's own ending is written here rather than by the bridge,
# because there is no bridge yet — nothing has been built for a call that never happened, and
# nothing is waiting to be unwound.
async def _the_far_end_answered(
    ctx: JobContext, worker: Worker, context: CallContext, arrival: router.Arrival
) -> bool:
    """Place the leg a dispatch asked for, and say whether there is a call to run."""
    if arrival.direction != "outbound":
        return True
    wanted = dialling.asked_of(arrival.metadata.get(DIAL_KEY))
    if wanted is None:
        await _never_answered(worker.gateway, context.call, "dial_failed")
        return False
    reason = await dialling.placed(ctx.api, ctx.room.name, wanted)
    if reason is None:
        return True
    await _never_answered(worker.gateway, context.call, reason)
    return False


async def _never_answered(gateway: Gateway, call: str, reason: defs.EndReason) -> None:
    """call.ended in the protocol's own word for it, and the log sealed. Nothing followed it."""
    ended = CallEnded(reason=reason, ended_by="platform", ended_at=time.time(), duration_s=0.0)
    await gateway.append(call, "call.ended", encode(ended))
    await gateway.sealed(call)


def letting_go(reading: asyncio.Task[None]) -> Callable[[str], Coroutine[None, None, None]]:
    """The shutdown callback that closes the command stream: the call is over."""

    async def stop(reason: str) -> None:  # noqa: ARG001 — livekit hands every callback the reason
        reading.cancel()

    return stop


def sealing(
    gateway: Gateway, bridge: Bridge, call: str, stopping: Stopping | None = None
) -> Callable[[str], Coroutine[None, None, None]]:
    """The shutdown callback: the recording is closed, the bridge says how the call ended, the
    log is sealed."""

    async def seal(reason: str) -> None:
        # Before the summary and never after it: the summary is the one place the pointer to the
        # audio is stated, and a pointer to a file the recorder has not finished writing reads,
        # at the door, as a recording that is on another box.
        if stopping is not None:
            await stopping()
        await bridge.closed(reason)
        await gateway.sealed(call)

    return seal


# Never in the way of a real call: a gateway that cannot answer this, or answers it wrongly, leaves
# the call in production, where it would have been without the question.
async def _a_developers(gateway: Gateway, route: Route, caller: str) -> str | None:
    """The developer whose sandbox copy takes this production ring, or None."""
    try:
        developer = await gateway.rings_for(route.agent, org=route.org, caller=caller)
    except Exception:  # noqa: BLE001 — any refusal is production's answer
        logger.warning(
            "could not ask whose phone is dialling %s; the call stays in production", route.agent
        )
        return None
    if developer is not None:
        logger.info(
            "a production call to %s from ···%s rings in %s's sandbox copy",
            route.agent,
            caller[-3:],
            developer,
        )
    return developer
