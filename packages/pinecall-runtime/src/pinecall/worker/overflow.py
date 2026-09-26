"""The overflow agent: on the hub, full until the fleet is, then one sentence, a number, hang up."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine

from livekit.agents import AgentServer, JobContext
from livekit.agents.voice import Agent, AgentSession
from livekit.protocol.room import DeleteRoomRequest

from pinecall._settings import Settings, load_settings
from pinecall.fleet import HEARTBEAT_S
from pinecall.worker import job_target
from pinecall.worker.gateway_client import Gateway
from pinecall.worker.gateway_http import GatewayRefused
from pinecall.worker.job import Worker, build_call_context
from pinecall.worker.main import build_worker
from pinecall_protocol import encode
from pinecall_protocol.events import AgentTranscript, CallEnded

logger = logging.getLogger(__name__)

# The overflow says one sentence: the one speech its transcript entry names.
THE_ONE_SENTENCE = "sp_1"

# livekit picks a worker at random, weighted by 1 − load, among the ones under its line
# (livekit-server pkg/service/agentservice.go, selectWorkerWeightedByLoad). So this worker cannot
# sit at 0.69 and hope to be picked last: it would take a third of the calls of a half-full fleet.
# It reports FULL — no dispatch reaches it — until the hub says every real worker is, and then
# NOTHING, so it is the one worker livekit can still hand the call to.
CLOSED = 1.0
OPEN = 0.0

# A phone caller's own number is what the callback needs; a web visitor who raced the fleet to a
# room is told the sentence and nothing is written — the widget had its 503 path for that.
THE_PHONE = "phone"

# A phone leg is seated before the job is dispatched and a web visitor joins within a second of
# minting; a room still empty after this long is a room nobody is coming to, and the sentence
# would play to nobody until the room's own empty timeout ended the job (measured: 73 s hung,
# 2026-09-11, the first dispatch into an empty room).
A_CALLER_MAY_TAKE_S = 15.0


class OverflowGate:
    """The load this worker reports: full, unless the hub said the fleet is."""

    def __init__(self) -> None:
        self.fleet_is_full = False

    def __call__(self, _server: AgentServer) -> float:
        """What livekit reads every half second."""
        return OPEN if self.fleet_is_full else CLOSED


class Watching:
    """The task that asks the hub every heartbeat whether the fleet is full, and opens the gate."""

    def __init__(self, gate: OverflowGate, gateway: Gateway) -> None:
        self._gate = gate
        self._gateway = gateway
        # Held here: a task nothing references may be collected by Python mid-loop.
        self._watching: asyncio.Task[None] | None = None

    def start_with(self, server: AgentServer) -> None:
        """Begin the moment the worker is up; end with the process."""
        server.on("worker_started", self._begin)  # pyright: ignore[reportUnknownMemberType]

    def _begin(self) -> None:
        """The loop, as a task this object holds for the life of the process."""
        self._watching = asyncio.create_task(self.run(), name="overflow-watch")

    async def run(self) -> None:
        """Poll until the process ends."""
        while True:
            await self._once()
            await asyncio.sleep(HEARTBEAT_S)

    async def _once(self) -> None:
        """One question to the hub; a hub that does not answer keeps the gate as it was."""
        try:
            full = await self._gateway.fleet_is_full()
        except GatewayRefused as refused:
            logger.warning("standing: %s", refused)
            return
        if full != self._gate.fleet_is_full:
            self._gate.fleet_is_full = full
            logger.warning(
                "the fleet is %s: this worker %s",
                "full" if full else "not full",
                "answers" if full else "is closed",
            )


# livekit hands a job process the entrypoint by name (worker/main.py explains), so it is a
# module-level function that builds what it needs from the environment it inherited.
async def job(ctx: JobContext) -> None:
    """The entrypoint of every overflow job: this process's Worker, then the caller is told."""
    settings = load_settings()
    await answer_the_overflow(ctx, build_worker(settings), settings.overflow_says)


def build_overflow_server(settings: Settings, gateway: Gateway) -> AgentServer:
    """The overflow process: under the fleet's own name, so a dispatch nobody else takes is its."""
    gate = OverflowGate()
    server = AgentServer(
        ws_url=settings.livekit_url,
        api_key=settings.livekit_api_key,
        api_secret=settings.livekit_api_secret,
        load_fnc=gate,
        # Any free port: nothing reads this server, and the box's own worker holds the fixed one.
        host="127.0.0.1",
        port=0,
    )
    server.rtc_session(job, agent_name=settings.fleet)
    Watching(gate, gateway).start_with(server)
    return server


# The same first steps as a real call — the room, the routes, who it is for, the call's log opened
# on the gateway — so the tenant sees this call in its log like any other, with what was said. Then
# one sentence over the agent's own voice, the number onto the agent's log, and the room deleted,
# which is what hangs up a SIP leg. No STT, no model: nothing here listens.
async def answer_the_overflow(ctx: JobContext, worker: Worker, says: str) -> None:
    """One overflow job: tell the caller, take their number, hang up, seal the log."""
    began = time.monotonic()
    ctx.log_context_fields = {"room": ctx.job.room.name}
    _, routes = await asyncio.gather(ctx.connect(), worker.gateway.routes())
    arrival = await job_target.arrival_of(ctx.job, ctx.room)
    route = job_target.resolve(arrival, routes, worker.default_agent)
    config, brought = await asyncio.gather(
        worker.gateway.agent(route.agent), worker.gateway.provider_keys(route.agent)
    )
    context = build_call_context(ctx.room.name or ctx.job.id, arrival, route, worker.timezone)
    await worker.gateway.opened(context, route.agent)
    ctx.add_shutdown_callback(_sealing(worker.gateway, context.call, began))
    if not await _somebody_arrived(ctx):
        logger.warning("nobody joined %s in %.0fs: leaving", ctx.room.name, A_CALLER_MAY_TAKE_S)
        await ctx.api.room.delete_room(DeleteRoomRequest(room=ctx.room.name))
        return
    voice = worker.kit(config, brought).tts
    session: AgentSession[None] = AgentSession(tts=voice)
    await session.start(Agent(instructions=says), room=ctx.room)  # pyright: ignore[reportUnknownMemberType]
    await session.say(says, allow_interruptions=False)
    said = AgentTranscript(speech_id=THE_ONE_SENTENCE, text=says, final=True)
    await worker.gateway.append(context.call, "agent.transcript", encode(said))
    if route.channel == THE_PHONE and arrival.caller:
        await worker.gateway.callback_requested(
            route.agent, route.channel, arrival.caller, context.call
        )
    await ctx.api.room.delete_room(DeleteRoomRequest(room=ctx.room.name))


async def _somebody_arrived(ctx: JobContext) -> bool:
    """Whether a participant is in the room, waited for briefly: the one the sentence is for."""
    try:
        async with asyncio.timeout(A_CALLER_MAY_TAKE_S):
            await ctx.wait_for_participant()
    except TimeoutError:
        return False
    return True


def _sealing(
    gateway: Gateway, call: str, began: float
) -> Callable[[str], Coroutine[None, None, None]]:
    """The shutdown callback: call.ended by the agent, then the log is closed."""

    async def seal(_reason: str) -> None:
        ended = CallEnded(
            reason="agent_hung_up",
            ended_by="agent",
            ended_at=time.time(),
            duration_s=time.monotonic() - began,
        )
        await gateway.append(call, "call.ended", encode(ended))
        await gateway.sealed(call)

    return seal
