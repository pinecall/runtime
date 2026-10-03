"""The worker process: livekit's AgentServer under its fleet's name, and the fleet's overflow."""

import asyncio
import logging
import signal
import time

from livekit.agents import AgentServer, JobContext, JobProcess
from livekit.agents.voice import Agent, AgentSession
from livekit.protocol.room import DeleteRoomRequest
from pydantic import TypeAdapter

from pinecall.channels.rooms import Dispatch, read_dispatch, room_closed
from pinecall.domain.errors import GatewayRefused, SettingsRefused
from pinecall.domain.scope import Scope
from pinecall.fleet.client import GatewayClient, gateway_at
from pinecall.fleet.heartbeat import (
    CORDONED_EXIT,
    Heartbeats,
    Load,
    agent_name_of,
    announced_ready,
)
from pinecall.fleet.measures import LastMinute, listening, measures_path
from pinecall.fleet.roster import overflow_name
from pinecall.process.settings import Settings, load
from pinecall.providers.build import installed, tts_of
from pinecall.providers.credentials import Pipeline
from pinecall.session.call import Writing
from pinecall.session.session import SEAL_S
from pinecall.wire.events import AgentTranscript, CallEnded
from pinecall.wire.rest.calls import CallbackRequest, OpenCallRequest, SealCallRequest
from pinecall.worker._job import (
    answer,
    arrival_of,
    context_of,
    ended_and_sealed,
    named_by,
    resolve,
    writer_of,
)
from pinecall.worker._traces import traced_to

logger = logging.getLogger(__name__)


# Under the unit's TimeoutStopSec (15 min): livekit's hour would end in a SIGKILL and calls with
# no call.ended. What is still running after it is shut down and sealed as drained.
DRAIN_S = 10 * 60


# What a job has to seal once told to stop: livekit's 10 s kills a seal halfway.
SEALING_S = 60.0

# A new process warms up while others are busy: livekit's own 10 s kills it while three pools warm
# up at once on a loaded box, then respawns it for ever.
INITIALIZE_S = 90.0


STILL_UP = "%d calls outlived the drain of %.0f s: shut down and sealed as drained"


NO_LIVEKIT = "LIVEKIT_URL, LIVEKIT_API_KEY and LIVEKIT_API_SECRET: a worker registers with them"


_STAGES: TypeAdapter[Pipeline] = TypeAdapter(Pipeline)


# The overflow is offered a call only by the gateway, by its name, when no worker of the fleet
# has a seat: it never reads itself full.
OPEN = 0.0


# A room still empty after this is left; otherwise the job waits for the room's own timeout.
A_CALLER_MAY_TAKE_S = 15.0


THE_ONE_SENTENCE = "sp_1"


NOT_TOLD = "call %s: the caller its worker left was not told all of it; the room closes anyway"


NOT_SEALED = "call %s: the gateway did not take the seal; its reaper seals the call"


# Module-level: livekit pickles the entrypoint by module and name, so the gateway it reaches is
# built in the job's own process.
async def job(ctx: JobContext) -> None:
    """Every job of the fleet: one call, or telling the caller of a call whose worker went away."""
    settings = load()
    gateway = _gateway_of(ctx.proc, settings)
    dispatch = read_dispatch(ctx.job.metadata)
    if dispatch.worker_gone:
        await _sentence_job(ctx, gateway, settings, dispatch)
        return
    await answer(ctx, gateway, settings)


# A process is warmed before a job reaches it: the gateway's client and the tracer are its own.
def prewarm(proc: JobProcess) -> None:
    """Ready a process for its jobs."""
    settings = load()
    proc.userdata["gateway"] = gateway_at(settings.gateway_url, settings.worker_key)
    traced_to(settings)


def server_of(settings: Settings) -> AgentServer:
    """The AgentServer of this worker, registered under its own name in its fleet."""
    _refuse_an_unregistrable(settings)
    # Every plugin imported in the worker's own process is one livekit lists in its forkserver's
    # preload: imported once there, inherited by each call's process. Imported in the call's
    # process instead, the 44 held its loop 3-5 s on an idle box, up to 70 s on a loaded one,
    # while the caller waited (infra/lab/, 2026-10-01).
    installed()
    # URL and pair are handed over: AgentServer reads os.environ, and the .env files are not in it.
    server = AgentServer(
        ws_url=settings.livekit_url,
        api_key=settings.livekit_api_key,
        api_secret=settings.livekit_api_secret,
        drain_timeout=DRAIN_S,
        shutdown_process_timeout=SEALING_S,
        initialize_process_timeout=INITIALIZE_S,
        # livekit's default health port, 8081, is the SIP service's on the box.
        host="127.0.0.1",
        port=settings.worker_http_port,
        setup_fnc=prewarm,
        # LiveKit's own line stands, on both of its sides; Load reports on its scale.
        load_fnc=Load(settings.max_jobs),
    )
    # livekit keeps one warm process per CPU; two fleets on one machine hold twice the memory.
    if settings.idle_processes is not None:
        server.update_options(num_idle_processes=settings.idle_processes)
    # The gateway chooses the worker and dispatches to its name (gateway/dispatching/).
    server.rtc_session(job, agent_name=agent_name_of(settings))
    return server


async def run(settings: Settings) -> int:
    """Run the worker until it is told to stop or cordoned; the exit it leaves with."""
    server = server_of(settings)
    gateway = gateway_at(settings.gateway_url, settings.worker_key)
    minute = LastMinute()
    hearing = await listening(measures_path(settings), minute)
    beats = Heartbeats(server, gateway, settings, minute)
    stopping = _stop_on_a_signal()
    running = asyncio.create_task(server.run())
    beating = asyncio.create_task(beats.run())
    ready = asyncio.create_task(announced_ready(beats, settings.notify_socket))
    waits = {running, asyncio.create_task(stopping.wait()), asyncio.create_task(beats.leave.wait())}
    await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
    try:
        await server.drain(DRAIN_S)
    except TimeoutError:
        # livekit raises when the drain runs out; the close is what shuts and seals the calls left.
        logger.warning(STILL_UP, len(server.active_jobs), DRAIN_S)
    await server.aclose()
    for task in (*waits, beating, ready):
        task.cancel()
    hearing.close()
    measures_path(settings).unlink(missing_ok=True)
    await gateway.aclose()
    return CORDONED_EXIT if beats.cordoned else 0


async def overflow_job(ctx: JobContext) -> None:
    """One call the fleet had no seat for: one sentence, a call back offered, the room closed."""
    settings = load()
    gateway = _gateway_of(ctx.proc, settings)
    began = time.monotonic()
    ctx.log_context_fields = {"room": ctx.job.room.name}
    dispatch = read_dispatch(ctx.job.metadata)
    if dispatch.worker_gone:
        await _sentence_job(ctx, gateway, settings, dispatch)
        return
    await ctx.connect()
    arrival = await arrival_of(dispatch, ctx.room)
    number = arrival.number if dispatch.org is None else None
    named = named_by(dispatch)
    found = await gateway.routes(named, number=number, channel=arrival.channel)
    route = resolve(dispatch, arrival, found, settings.agent)
    scope = Scope(route.org, route.env, dispatch.holder or "")
    stages = _STAGES.validate_python(await gateway.stages(route.agent, scope))
    context = context_of(ctx, dispatch, arrival, route, settings)
    await gateway.open(OpenCallRequest(agent=route.agent, context=context))
    writing = writer_of(gateway, context.call)
    try:
        await _said_once(ctx, writing, stages, settings.overflow_says)
        if route.channel == "phone" and arrival.caller:
            wanted = CallbackRequest(
                agent=route.agent, channel=route.channel, number=arrival.caller, call=context.call
            )
            await gateway.callback(wanted)
    finally:
        # Deleting the room is what hangs up a SIP leg.
        await ctx.api.room.delete_room(DeleteRoomRequest(room=ctx.room.name))
        ended = CallEnded(
            reason="agent_hung_up",
            ended_by="agent",
            ended_at=time.time(),
            duration_s=time.monotonic() - began,
        )
        await ended_and_sealed(gateway, writing, ended, settings.overflow_says)


def always_open(_server: AgentServer) -> float:
    """The overflow's load: it never reads itself full."""
    return OPEN


def overflow_of(settings: Settings) -> AgentServer:
    """The overflow's AgentServer, under <fleet>/overflow, on any free port, always open."""
    _refuse_an_unregistrable(settings)
    # Preloaded in the forkserver, as for the worker's own server.
    installed()
    server = AgentServer(
        ws_url=settings.livekit_url,
        api_key=settings.livekit_api_key,
        api_secret=settings.livekit_api_secret,
        host="127.0.0.1",
        port=0,
        load_fnc=always_open,
        setup_fnc=prewarm,
        initialize_process_timeout=INITIALIZE_S,
    )
    server.rtc_session(overflow_job, agent_name=overflow_name(settings.fleet))
    return server


async def overflow(settings: Settings) -> int:
    """Run the overflow until told to stop: the gateway sends it calls no worker has a seat for."""
    server = overflow_of(settings)
    stopping = _stop_on_a_signal()
    running = asyncio.create_task(server.run())
    await asyncio.wait(
        {running, asyncio.create_task(stopping.wait())}, return_when=asyncio.FIRST_COMPLETED
    )
    await server.aclose()
    return 0


def sentence_entry(says: str) -> AgentTranscript:
    """The overflow's one sentence as the call's transcript holds it."""
    return AgentTranscript(speech_id=THE_ONE_SENTENCE, text=says, final=True)


# The gateway ended the call as drained before sending this job, and offered the call back; the
# job's writer takes the call over where the dead worker's stopped. A gateway that no longer
# serves the call refuses the entry and the seal, and its reaper seals it.
async def _sentence_job(
    ctx: JobContext, gateway: GatewayClient, settings: Settings, dispatch: Dispatch
) -> None:
    call = ctx.job.room.name
    ctx.log_context_fields = {"room": call}
    scope = named_by(dispatch)
    await ctx.connect()
    says = settings.overflow_says
    writing = writer_of(gateway, call, after=dispatch.entries_written)
    try:
        if scope is not None and dispatch.agent is not None:
            stages = _STAGES.validate_python(await gateway.stages(dispatch.agent, scope))
            await _said_once(ctx, writing, stages, says)
    except GatewayRefused:
        logger.warning(NOT_TOLD, call, exc_info=True)
    finally:
        await room_closed(ctx.api, call)
    await writing.close(SEAL_S)
    try:
        await gateway.sealed(call, SealCallRequest(usage=[], outcome=says))
    except GatewayRefused:
        logger.warning(NOT_SEALED, call, exc_info=True)


async def _said_once(ctx: JobContext, writing: Writing, stages: Pipeline, says: str) -> None:
    try:
        async with asyncio.timeout(A_CALLER_MAY_TAKE_S):
            await ctx.wait_for_participant()
    except TimeoutError:
        logger.warning("nobody joined %s in %.0fs: leaving", ctx.room.name, A_CALLER_MAY_TAKE_S)
        return
    session: AgentSession[None] = AgentSession(tts=tts_of(stages.tts))
    await session.start(Agent(instructions=says), room=ctx.room, record=False)  # pyright: ignore[reportUnknownMemberType]
    await session.say(says, allow_interruptions=False)
    writing.write("agent.transcript", sentence_entry(says))
    await session.aclose()


def _gateway_of(proc: JobProcess, settings: Settings) -> GatewayClient:
    warmed: object = proc.userdata.get("gateway")
    if isinstance(warmed, GatewayClient):
        return warmed
    return gateway_at(settings.gateway_url, settings.worker_key)


def _refuse_an_unregistrable(settings: Settings) -> None:
    if not settings.livekit_url or not settings.livekit_api_key or not settings.livekit_api_secret:
        raise SettingsRefused(NO_LIVEKIT)


def _stop_on_a_signal() -> asyncio.Event:
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for stop in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(stop, stopping.set)
    return stopping
