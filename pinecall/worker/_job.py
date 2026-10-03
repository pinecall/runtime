"""One job, one call: whose it is, what it runs on, the room it runs in, and its end."""

import asyncio
import logging
import os
import tempfile
import time
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from datetime import timedelta
from functools import partial
from pathlib import Path

from livekit import api, rtc
from livekit.agents import JobContext
from livekit.protocol.sip import CreateSIPParticipantRequest
from pydantic import TypeAdapter

from pinecall.channels.rooms import Dialling, Dispatch, dispatched, read_dispatch, room_closed
from pinecall.channels.telephony.dialing import sip_config
from pinecall.domain.agent import AgentConfig
from pinecall.domain.call import CallContext, Contact, Route, today_in
from pinecall.domain.errors import GatewayRefused, NotFound, PinecallError
from pinecall.domain.names import (
    CHANNELS_WITH_A_NUMBER,
    PRODUCTION,
    SANDBOX,
    THE_WIDGET,
    Channel,
    Direction,
    JsonObject,
)
from pinecall.domain.scope import SCOPE_ATTRIBUTE, Scope
from pinecall.fleet.client import GatewayClient, again
from pinecall.fleet.heartbeat import worker_name_of
from pinecall.fleet.measures import measures_path, reported
from pinecall.process.settings import Settings
from pinecall.providers.credentials import Pipeline
from pinecall.session import clock, room
from pinecall.session.call import Call, Platform, Seal, ToolUse, Writing
from pinecall.session.hold import HoldMusic
from pinecall.session.session import SEAL_S, Session
from pinecall.session.text import text_session
from pinecall.session.voice import voice_session
from pinecall.session.widget import Reading, Widget
from pinecall.wire.commands import command_of
from pinecall.wire.events import CallEnded, ErrorEvent, ToolCall
from pinecall.wire.frames import Command, Entry
from pinecall.wire.metrics import ModelUsage
from pinecall.wire.parts import EndedBy, EndReason, PlatformTool, ToolResult
from pinecall.wire.rest.calls import (
    BatchedEntry,
    OpenCallRequest,
    OpenCallResponse,
    SealCallRequest,
)
from pinecall.wire.state import State
from pinecall.worker._recorder import recording_path, stored, written

logger = logging.getLogger(__name__)


_STAGES: TypeAdapter[Pipeline] = TypeAdapter(Pipeline)


# The one scope that makes a visit written: a chat in the widget, no microphone.
WRITTEN_SCOPE = "chat"

# How long a chat in a room waits for the person's next message: the voice ceiling's ten minutes.
# A visitor who left the page open held a seat for over an hour on 2026-10-03.
A_CHAT_WAITS_S = 600.0


# The talk seat may join after the agent; a session started with nobody seated records nothing.
WAIT_FOR_THE_CALLER_S = 5.0


# Clips are cached by their hash: each is fetched once per box.
HOLD_CACHE = Path(tempfile.gettempdir()) / "pinecall-hold"


NO_ROUTE = "the job names no agent, no number was dialled, and this worker has no default agent"


NOBODY_AT = "nobody answers {number} on {channel}"


NO_DOOR = "agent {agent} answers no {channel} door: it looked in {looked}"


# SIP answers: busy and declined; the three ways of nobody picking up.
BUSY = {"486", "603"}


NO_ANSWER = {"408", "480", "487"}


ANSWERED_IN = "the pipeline is live %.2fs after the job arrived"


@dataclass(frozen=True)
class Arrival:
    """What a job says of its call before it runs: who calls, through what, dialling what."""

    caller: str
    channel: Channel
    direction: Direction
    # The number dialled, read off the caller's SIP leg: phone jobs are room jobs, and livekit
    # leaves job.participant empty for them.
    number: str | None = None


async def answer(ctx: JobContext, gateway: GatewayClient, settings: Settings) -> None:
    """Answer the job: join, find whose call it is, open its log, and run it until it ends."""
    began = time.monotonic()
    ctx.log_context_fields = {"room": ctx.job.room.name}
    dispatch = read_dispatch(ctx.job.metadata)
    named = named_by(dispatch)
    # The org's routes are fetched while the room connects; the dialled number waits for it.
    _, found = await asyncio.gather(
        ctx.connect(), gateway.routes(named, number=None, channel="phone")
    )
    arrival = await arrival_of(dispatch, ctx.room)
    if arrival.number is not None and named is None:
        found = await gateway.routes(None, number=arrival.number, channel=arrival.channel)
    route = resolve(dispatch, arrival, found, settings.agent)
    if may_be_a_developers(dispatch, arrival, route) and await _handed_over(
        ctx, gateway, dispatch, arrival, route
    ):
        return
    scope = Scope(route.org, route.env, dispatch.holder or "")
    context = context_of(ctx, dispatch, arrival, route, settings)
    config, stages, played = await asyncio.gather(
        gateway.agent(route.agent, scope, call=context.call),
        gateway.stages(route.agent, scope, call=context.call),
        gateway.hold_audio(route.agent, scope),
    )
    opened = await gateway.open(
        OpenCallRequest(agent=route.agent, context=context, app=dispatch.app or settings.app)
    )
    # An outbound call is its far end answering: nothing is built for a leg that never came up.
    if dispatch.dial is not None and not await _answered(ctx, gateway, context, dispatch.dial):
        return
    typed = dispatch.scope == WRITTEN_SCOPE
    audio = _recorded(config, settings, context.call, typed=typed)
    pipeline = _STAGES.validate_python(stages)

    # The session closed its recorder before it seals: the summary points at a whole file.
    async def ended(usage: list[ModelUsage], outcome: str) -> None:
        kept = await _kept(audio, partial(stored, settings, gateway, route.org, context.call))
        data = SealCallRequest(usage=usage, outcome=outcome, recording=kept, lent=pipeline.lent)
        await gateway.sealed(context.call, data)

    measures = measures_path(settings)
    call = Call(context, config, _platform(gateway, context.call, config, ended, measures), audio)
    session = text_session(call, pipeline.llm) if typed else voice_session(call, pipeline)

    # Registered before anything else can fail: a call that dies in its setup still seals.
    # livekit reads a shutdown callback's `__code__`, which a `partial` has not.
    async def closed(_reason: str) -> None:
        await session.close()

    ctx.add_shutdown_callback(closed)
    where = room.CallRoom(
        call,
        ctx.room,
        ctx.api,
        trunks=partial(_trunk_for, gateway, context),
        claim=partial(_claimed, gateway, context.call),
    )
    hold = await _hold_music(gateway, route.agent, scope, played.played, played.sha256)
    await session.start(
        where=where,
        hold=hold,
        seat=await _seat_of(ctx.room, route.channel, typed=typed),
        opening=None if typed else opening_of(opened, recorded=audio is not None),
        worker=worker_name_of(settings),
    )
    if hold is not None:
        await hold.start(ctx.room)
    where.watch()

    # A call the session ended already (a transfer, the model's goodbye) is not ended again.
    def caller_gone() -> None:
        if session.ended is None:
            session.hang_up("caller_hung_up", "caller")

    where.when_the_caller_is_gone(caller_gone)
    widget = _widget(gateway, call, ctx.room) if route.channel == THE_WIDGET else None
    logger.info(ANSWERED_IN, time.monotonic() - began)
    commands = asyncio.create_task(_commands(gateway, session, context.call))
    limit = config.max_duration_s if route.channel in CHANNELS_WITH_A_NUMBER or not typed else 0
    ceiling = 0 if opened.seconds_left is None else opened.seconds_left
    kept = min(item for item in (limit, ceiling) if item) if limit or ceiling else 0
    timer = asyncio.create_task(clock.keep_time(session, kept, exhausted=None))
    # A chat in a room is the one call with no ceiling: it ends after ten quiet minutes instead.
    quiet = typed and route.channel not in CHANNELS_WITH_A_NUMBER
    silence = asyncio.create_task(clock.end_when_quiet(session, A_CHAT_WAITS_S if quiet else 0))

    ctx.add_shutdown_callback(_letting_go(ctx, session, where, widget, commands, timer, silence))


# The agent leaving ends no SIP leg: with the room still up, a phone caller hears a silent line
# until they hang up themselves. A transfer leaves them with the far end and a handover with the
# next worker, so only a call its session hung up takes the room with it (the model's end_call
# already did, and a room gone is fine).
async def room_over(
    server: api.LiveKitAPI, name: str, ended: tuple[EndReason, EndedBy] | None
) -> None:
    """Delete the room once the session hung up the call for any reason but a transfer."""
    if ended is not None and ended[0] != "transferred":
        await room_closed(server, name)


async def arrival_of(dispatch: Dispatch, where: rtc.Room) -> Arrival:
    """What the job's dispatch and the room's SIP leg say of the call."""
    outbound = dispatch.direction == "outbound"
    # A dispatch that names its agent does not wait for a SIP leg; an outbound job places it.
    leg = None if dispatch.agent or outbound else await room.caller_leg(where, "phone")
    data = {} if leg is None else dict(leg.attributes)
    dialled = data.get(room.DIALLED_NUMBER)
    return Arrival(
        caller=data.get(room.CALLER_NUMBER) or dispatch.caller or where.name,
        channel="phone" if outbound or dialled else THE_WIDGET,
        direction="outbound" if outbound else "inbound",
        number=dialled,
    )


# The dispatch's agent first, then the number dialled, then the worker's own default.
def resolve(
    dispatch: Dispatch, arrival: Arrival, routes: list[Route], default: str | None
) -> Route:
    """The route the call takes."""
    agent = dispatch.agent or (None if arrival.number else default)
    if agent is not None:
        return _of_agent(agent, dispatch, arrival, routes)
    if arrival.number is None:
        raise NotFound(NO_ROUTE)
    door = (arrival.channel, arrival.number)
    found = next((route for route in routes if route.door == door), None)
    if found is None:
        raise NotFound(NOBODY_AT.format(number=arrival.number, channel=arrival.channel))
    return found


# Only an undispatched ring at a production number can be a developer's own phone.
def may_be_a_developers(dispatch: Dispatch, arrival: Arrival, route: Route) -> bool:
    """Whether to ask if this ring belongs in a developer's sandbox."""
    return (
        arrival.number is not None
        and dispatch.agent is None
        and arrival.direction == "inbound"
        and route.channel == "phone"
        and route.env == PRODUCTION
    )


def named_by(dispatch: Dispatch) -> Scope | None:
    """The scope the dispatch names, or None on the box's own trunk, where the number decides."""
    if dispatch.org is None or dispatch.env is None:
        return None
    return Scope(dispatch.org, dispatch.env, dispatch.holder or "")


def context_of(
    ctx: JobContext, dispatch: Dispatch, arrival: Arrival, route: Route, settings: Settings
) -> CallContext:
    """The call as the gateway opens it, from the job, its dispatch and its route."""
    return CallContext(
        # The room is the call: its name is the id the gateway minted, or the SIP rule's.
        call=ctx.room.name or ctx.job.id,
        channel=route.channel,
        direction=arrival.direction,
        caller=arrival.caller,
        route=route,
        today=today_in(settings.timezone),
        contact=None if dispatch.contact is None else Contact(id=dispatch.contact),
        metadata=dispatch.metadata,
        run=dispatch.run,
        persona=dispatch.persona,
        accepts_when=dispatch.accepts_when,
        declines_when=dispatch.declines_when,
        holder=dispatch.holder,
    )


# The SIP answer livekit passes on: busy and declined, no answer, anything else a dial that failed.
def end_reason_of(refused: Exception) -> EndReason:
    """The log's own word for a leg that never came up."""
    answer = (
        refused.metadata.get("sip_status_code") if isinstance(refused, api.TwirpError) else None
    )
    if answer in BUSY:
        return "busy"
    if answer in NO_ANSWER:
        return "no_answer"
    return "dial_failed"


# A widget's route is not stored: every agent answers on the widget, and the token door already
# checked the agent is the key's. A handed-over ring dialled production's number.
# The org's sentences said before the greeting: the disclosure, then the notice when it records.
def opening_of(opened: OpenCallResponse, *, recorded: bool) -> str | None:
    """What a spoken call says before its greeting, or None."""
    notice = opened.recording_notice if recorded else None
    return " ".join(item for item in (opened.disclosure, notice) if item) or None


# A call has one writer from its open to its seal: a job that ends a call no session runs writes
# through one too, following on from whatever the log took from the writer before it.
def writer_of(gateway: GatewayClient, call: str, *, after: int = 0) -> Writing:
    """The call's writer on the gateway's batch door, sending."""
    writing = Writing(partial(gateway.append_many, call), call, after=after)
    writing.open()
    return writing


async def ended_and_sealed(
    gateway: GatewayClient, writing: Writing, ended: CallEnded, outcome: str
) -> None:
    """End a call no session ran: its `call.ended` written once, what is queued sent, the seal."""
    writing.write("call.ended", ended)
    await writing.close(SEAL_S)
    await gateway.sealed(writing.call, SealCallRequest(usage=[], outcome=outcome))


# The refusal is the call's own entry, written by its writer like any other.
async def applied(session: Session, command: Command) -> None:
    """Apply one of the app's commands; a refusal is an `error` entry on the call."""
    try:
        await session.apply(command_of(command))
    except PinecallError as refused:
        text = ErrorEvent(
            code="refused",
            message=str(refused),
            command=command.type,
            id=command.id,
            recoverable=True,
        )
        session.call.writing.write("error", text)


def _of_agent(agent: str, dispatch: Dispatch, arrival: Arrival, routes: list[Route]) -> Route:
    if dispatch.diverted_from is not None and arrival.number and dispatch.org and dispatch.env:
        return Route(
            org=dispatch.org,
            agent=agent,
            channel=arrival.channel,
            number=arrival.number,
            env=dispatch.env,
        )
    found = next(
        (route for route in routes if route.agent == agent and route.channel == arrival.channel),
        None,
    )
    if found is not None:
        return found
    if arrival.channel == THE_WIDGET and dispatch.org and dispatch.env:
        return Route(org=dispatch.org, agent=agent, channel=THE_WIDGET, env=dispatch.env)
    survey = sorted({f"{route.org}/{route.env}" for route in routes}) or ["no org at all"]
    raise NotFound(NO_DOOR.format(agent=agent, channel=arrival.channel, looked=", ".join(survey)))


# The sandbox's fleet is sent into the same room: the caller and the trunk stay where they are.
async def _handed_over(
    ctx: JobContext, gateway: GatewayClient, dispatch: Dispatch, arrival: Arrival, route: Route
) -> bool:
    try:
        handed = await gateway.rings_for(route.agent, route.org, arrival.caller)
    except GatewayRefused:
        logger.warning("could not ask whose phone rings %s: the call stays here", route.agent)
        return False
    if handed.holder is None or handed.fleet is None:
        return False
    moved = dispatch.model_copy(
        update={
            "agent": route.agent,
            "org": route.org,
            "env": SANDBOX,
            "holder": handed.holder,
            "caller": arrival.caller,
            "diverted_from": PRODUCTION,
        }
    )
    try:
        await dispatched(ctx.api, ctx.room.name, handed.fleet, moved)
    except api.TwirpError:
        logger.warning("could not hand %s to %s: the call stays here", route.agent, handed.fleet)
        return False
    ctx.shutdown(reason=f"handed to {handed.fleet}")
    return True


def _recorded(config: AgentConfig, settings: Settings, call: str, *, typed: bool) -> Path | None:
    if typed or not config.record:
        return None
    return recording_path(Path(settings.recordings_root), call)


# What the call writes also tells its worker's heartbeat: first audio, errors, how it ended.
def _platform(
    gateway: GatewayClient, call: str, config: AgentConfig, seal: Seal, measures: Path
) -> Platform:
    async def append_many(entries: list[BatchedEntry], *, after: int) -> list[Entry]:
        reported(measures, entries)
        return await gateway.append_many(call, entries, after=after)

    async def tool(use: ToolUse, speech: str | None) -> ToolResult:
        spec = config.tools_by_name.get(use.name)
        data = ToolCall(
            call_id=use.call_id, name=use.name, arguments=use.arguments, speech_id=speech
        )
        timeout = 30.0 if spec is None else spec.timeout_s
        return await gateway.tool(call, config.slug, data.written(), timeout)

    async def lookup(
        tool_name: PlatformTool, arguments: JsonObject, speech: str | None
    ) -> JsonObject:
        return await gateway.lookup(call, tool_name, arguments, speech)

    return Platform(append_many=append_many, tool=tool, lookup=lookup, seal=seal)


async def _kept(audio: Path | None, store: Callable[[Path], Awaitable[Path]]) -> str | None:
    if audio is None or not await asyncio.to_thread(written, audio):
        return None
    return str(await store(audio))


# The platform wants a claim that answers nothing; the client's says whether a page was waiting.
async def _claimed(gateway: GatewayClient, call: str, code: str) -> None:
    await gateway.claim(call, code)


async def _trunk_for(gateway: GatewayClient, context: CallContext, to: str) -> room.Trunk:
    route = context.route
    scope = Scope(route.org, route.env, context.holder or "")
    leg = await gateway.leg(route.agent, scope, to=to, call=context.call, shown=route.number)
    return room.Trunk(config=sip_config(leg), shown=leg.shown)


# Waiting until answered is what tells busy and no answer apart; the media plane holds the
# ceiling, so a worker that dies does not leave the far end on the line.
async def _answered(
    ctx: JobContext, gateway: GatewayClient, context: CallContext, dial: Dialling
) -> bool:
    route = context.route
    scope = Scope(route.org, route.env, context.holder or "")
    request = CreateSIPParticipantRequest(
        sip_call_to=dial.to,
        sip_number=dial.shown,
        room_name=ctx.room.name,
        participant_identity=f"{room.LEG_PREFIX}{dial.to}",
        wait_until_answered=True,
    )
    request.ringing_timeout.FromTimedelta(timedelta(seconds=room.RINGING_S))
    if dial.max_duration_s:
        request.max_call_duration.FromTimedelta(timedelta(seconds=dial.max_duration_s))
    try:
        leg = await gateway.leg(route.agent, scope, to=dial.to, call=context.call, shown=dial.shown)
        request.trunk.CopyFrom(sip_config(leg))
        await ctx.api.sip.create_sip_participant(request)
    except (api.TwirpError, GatewayRefused) as refused:
        reason = end_reason_of(refused)
        logger.warning("the far end did not answer: %s", reason)
        ended = CallEnded(reason=reason, ended_by="platform", ended_at=time.time(), duration_s=0.0)
        await ended_and_sealed(gateway, writer_of(gateway, context.call), ended, reason)
        ctx.shutdown(reason=reason)
        return False
    return True


# Only the talk seat is the caller: a listener and a supervisor share the room.
async def _seat_of(where: rtc.Room, channel: Channel, *, typed: bool) -> str | None:
    if typed or channel not in {"phone", THE_WIDGET}:
        return None
    if channel == "phone":
        leg = await room.caller_leg(where, channel)
        return None if leg is None else leg.identity
    return await _talk_seat(where)


async def _talk_seat(where: rtc.Room) -> str | None:
    seated = _talker_in(where)
    if seated is not None or not where.isconnected():
        return seated
    joined: asyncio.Future[str] = asyncio.get_running_loop().create_future()

    def _if_the_caller(seat: rtc.RemoteParticipant) -> None:
        if seat.attributes.get(SCOPE_ATTRIBUTE) == "talk" and not joined.done():
            joined.set_result(seat.identity)

    where.on("participant_active", _if_the_caller)  # pyright: ignore[reportUnknownMemberType]
    try:
        # Asked again after listening: a join between the two reads would be missed.
        seated = _talker_in(where)
        if seated is not None:
            return seated
        async with asyncio.timeout(WAIT_FOR_THE_CALLER_S):
            return await joined
    except TimeoutError:
        return None
    finally:
        where.off("participant_active", _if_the_caller)  # pyright: ignore[reportUnknownMemberType]


def _talker_in(where: rtc.Room) -> str | None:
    return next(
        (
            seat.identity
            for seat in where.remote_participants.values()
            if seat.attributes.get(SCOPE_ATTRIBUTE) == "talk"
        ),
        None,
    )


# The default clip is the box's data, not the wheel's: a box that holds none plays silence.
async def _hold_music(
    gateway: GatewayClient, agent: str, scope: Scope, played: str, sha256: str | None
) -> HoldMusic | None:
    if played != "custom" or sha256 is None:
        return None
    kept = HOLD_CACHE / f"{sha256}.ogg"
    if not kept.is_file():
        try:
            audio = await gateway.hold_clip(agent, scope)
        except GatewayRefused:
            logger.warning("the hold clip of %s could not be fetched: the call plays none", agent)
            return None
        await asyncio.to_thread(_kept_beside, kept, audio)
    return HoldMusic(kept)


# Written beside and renamed, so a job reading the cache never reads half a file.
def _kept_beside(kept: Path, audio: bytes) -> None:
    kept.parent.mkdir(parents=True, exist_ok=True)
    part = kept.with_suffix(f".{os.getpid()}.part")
    part.write_bytes(audio)
    part.replace(kept)


# Only the gateway numbers a log, so a widget is sent what is read back from it.
def _widget(gateway: GatewayClient, call: Call, where: rtc.Room) -> Widget:
    async def state() -> tuple[State, int]:
        data = await gateway.state(call.context.call)
        return State.model_validate(data["state"]), int(str(data["last_seq"]))

    reading = Reading(
        state=state,
        since=partial(gateway.since, call.context.call),
        tail=partial(gateway.tail, call.context.call),
    )
    widget = Widget(call, where, reading)
    widget.watch()
    return widget


# A stream that ends means the gateway went away: it is opened again; a 4xx ends it.
async def _commands(gateway: GatewayClient, session: Session, call: str) -> None:
    async def listened() -> None:
        async for command in gateway.commands(call):
            await applied(session, command)

    await again(listened, None, f"the commands of {call}")


# A closure, not a partial: livekit reads a shutdown callback's `__code__`.
def _letting_go(
    ctx: JobContext,
    session: Session,
    where: room.CallRoom,
    widget: Widget | None,
    *tasks: asyncio.Task[None],
) -> Callable[[str], Coroutine[None, None, None]]:
    async def let_go(_reason: str) -> None:
        for task in tasks:
            task.cancel()
        where.stop()
        if widget is not None:
            widget.stop()
        await room_over(ctx.api, ctx.room.name, session.ended)

    return let_go
