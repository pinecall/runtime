"""The worker process: livekit's AgentServer under its fleet's name, and the fleet's overflow."""

import asyncio
import logging
import signal
import socket
import time

from livekit.agents import AgentServer, JobContext, JobProcess
from livekit.agents.voice import Agent, AgentSession
from livekit.protocol.room import DeleteRoomRequest
from pydantic import TypeAdapter

from pinecall.channels.rooms import read_dispatch
from pinecall.domain.errors import GatewayRefused, SettingsRefused
from pinecall.domain.scope import Scope
from pinecall.fleet.client import GatewayClient, gateway_at
from pinecall.fleet.heartbeat import CORDONED_EXIT, Heartbeats, Load
from pinecall.fleet.roster import HEARTBEAT_S
from pinecall.process.settings import Settings, load
from pinecall.providers.build import tts_of
from pinecall.providers.credentials import Pipeline
from pinecall.wire.events import AgentTranscript, CallEnded
from pinecall.wire.rest.calls import CallbackRequest, OpenCallRequest, SealCallRequest
from pinecall.worker._job import answer, arrival_of, context_of, named_by, resolve
from pinecall.worker._traces import traced_to

logger = logging.getLogger(__name__)


# Under the unit's TimeoutStopSec (15 min): livekit's hour would end in a SIGKILL and calls with
# no call.ended. What is still running after it is shut down and sealed as drained.
DRAIN_S = 10 * 60


# What a job has to seal once told to stop: livekit's 10 s kills a seal halfway.
SEALING_S = 60.0


NO_LIVEKIT = "LIVEKIT_URL, LIVEKIT_API_KEY and LIVEKIT_API_SECRET: a worker registers with them"


_STAGES: TypeAdapter[Pipeline] = TypeAdapter(Pipeline)


# livekit weighs workers by 1 - load: a middling load would still take calls. Full until the
# fleet is full, then empty.
CLOSED = 1.0


OPEN = 0.0


# A room still empty after this is left; otherwise the job waits for the room's own timeout.
A_CALLER_MAY_TAKE_S = 15.0


THE_ONE_SENTENCE = "sp_1"


class OverflowGate:
    """The overflow's load: full until the gateway says every worker of the fleet is."""

    def __init__(self) -> None:
        """Closed."""
        self.fleet_is_full = False

    def __call__(self, _server: AgentServer) -> float:
        """The load livekit reads twice a second."""
        return OPEN if self.fleet_is_full else CLOSED


# Module-level: livekit pickles the entrypoint by module and name, so the gateway it reaches is
# built in the job's own process.
async def job(ctx: JobContext) -> None:
    """Every job of the fleet: one call."""
    settings = load()
    await answer(ctx, _gateway_of(ctx.proc, settings), settings)


# A process is warmed before a job reaches it: the gateway's client and the tracer are its own.
def prewarm(proc: JobProcess) -> None:
    """Ready a process for its jobs."""
    settings = load()
    proc.userdata["gateway"] = gateway_at(settings.gateway_url, settings.worker_key)
    traced_to(settings)


def server_of(settings: Settings) -> AgentServer:
    """The AgentServer of this worker, registered under its fleet's name."""
    _refuse_an_unregistrable(settings)
    # URL and pair are handed over: AgentServer reads os.environ, and the .env files are not in it.
    server = AgentServer(
        ws_url=settings.livekit_url,
        api_key=settings.livekit_api_key,
        api_secret=settings.livekit_api_secret,
        drain_timeout=DRAIN_S,
        shutdown_process_timeout=SEALING_S,
        # livekit's default health port, 8081, is the SIP service's on the box.
        host="127.0.0.1",
        port=settings.worker_http_port,
        setup_fnc=prewarm,
        load_fnc=Load(settings.max_jobs),
    )
    # livekit keeps one warm process per CPU; two fleets on one machine hold twice the memory.
    if settings.idle_processes is not None:
        server.update_options(num_idle_processes=settings.idle_processes)
    server.rtc_session(job, agent_name=settings.fleet)
    return server


async def run(settings: Settings) -> int:
    """Run the worker until it is told to stop or cordoned; the exit it leaves with."""
    server = server_of(settings)
    gateway = gateway_at(settings.gateway_url, settings.worker_key)
    beats = Heartbeats(server, gateway, settings, settings.worker_name or _short_host())
    stopping = _stop_on_a_signal()
    running = asyncio.create_task(server.run())
    beating = asyncio.create_task(beats.run())
    waits = {running, asyncio.create_task(stopping.wait()), asyncio.create_task(beats.leave.wait())}
    await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
    await server.drain(DRAIN_S)
    await server.aclose()
    for task in (*waits, beating):
        task.cancel()
    await gateway.aclose()
    return CORDONED_EXIT if beats.cordoned else 0


async def overflow_job(ctx: JobContext) -> None:
    """One call the fleet had no seat for: one sentence, a call back offered, the room closed."""
    settings = load()
    gateway = _gateway_of(ctx.proc, settings)
    began = time.monotonic()
    ctx.log_context_fields = {"room": ctx.job.room.name}
    dispatch = read_dispatch(ctx.job.metadata)
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
    try:
        await _said_once(ctx, gateway, context.call, stages, settings.overflow_says)
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
        await gateway.append(context.call, "call.ended", ended.written())
        await gateway.sealed(
            context.call, SealCallRequest(usage=[], outcome=settings.overflow_says)
        )


def overflow_of(settings: Settings, gate: OverflowGate) -> AgentServer:
    """The overflow's AgentServer, under the fleet's name, on any free port."""
    _refuse_an_unregistrable(settings)
    server = AgentServer(
        ws_url=settings.livekit_url,
        api_key=settings.livekit_api_key,
        api_secret=settings.livekit_api_secret,
        host="127.0.0.1",
        port=0,
        load_fnc=gate,
        setup_fnc=prewarm,
    )
    server.rtc_session(overflow_job, agent_name=settings.fleet)
    return server


async def overflow(settings: Settings) -> int:
    """Run the overflow until told to stop, opening and closing with its fleet's standing."""
    gate = OverflowGate()
    server = overflow_of(settings, gate)
    gateway = gateway_at(settings.gateway_url, settings.worker_key)
    stopping = _stop_on_a_signal()
    running = asyncio.create_task(server.run())
    watching = asyncio.create_task(_watched(gate, gateway, settings.fleet))
    await asyncio.wait(
        {running, asyncio.create_task(stopping.wait())}, return_when=asyncio.FIRST_COMPLETED
    )
    watching.cancel()
    await server.aclose()
    await gateway.aclose()
    return 0


async def _said_once(
    ctx: JobContext, gateway: GatewayClient, call: str, stages: Pipeline, says: str
) -> None:
    try:
        async with asyncio.timeout(A_CALLER_MAY_TAKE_S):
            await ctx.wait_for_participant()
    except TimeoutError:
        logger.warning("nobody joined %s in %.0fs: leaving", ctx.room.name, A_CALLER_MAY_TAKE_S)
        return
    session: AgentSession[None] = AgentSession(tts=tts_of(stages.tts))
    await session.start(Agent(instructions=says), room=ctx.room, record=False)  # pyright: ignore[reportUnknownMemberType]
    await session.say(says, allow_interruptions=False)
    data = AgentTranscript(speech_id=THE_ONE_SENTENCE, text=says, final=True)
    await gateway.append(call, "agent.transcript", data.written())
    await session.aclose()


# A gateway that does not answer leaves the gate as it was.
async def _watched(gate: OverflowGate, gateway: GatewayClient, fleet: str) -> None:
    while True:
        try:
            full = await gateway.fleet_is_full(fleet)
        except GatewayRefused as refused:
            logger.warning("the fleet's standing: %s", refused)
        else:
            if full != gate.fleet_is_full:
                gate.fleet_is_full = full
                logger.warning("the fleet is %s", "full: the overflow answers" if full else "open")
        await asyncio.sleep(HEARTBEAT_S)


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


def _short_host() -> str:
    return socket.gethostname().split(".")[0]
