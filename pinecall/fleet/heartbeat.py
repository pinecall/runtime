"""Beside a worker's calls: the load livekit reads, and the heartbeat the gateway hears."""

import asyncio
import logging
import socket
import time
from collections.abc import Callable
from dataclasses import fields

from livekit.agents import AgentServer
from livekit.agents.worker import ServerOptions

from pinecall.domain.errors import DeclarationRefused, GatewayRefused
from pinecall.fleet.client import GatewayClient
from pinecall.fleet.measures import LastMinute
from pinecall.fleet.roster import HEARTBEAT_S, REFUSED_AT, agent_name
from pinecall.process.settings import Settings
from pinecall.wire.rest.fleet import HeartbeatRequest

logger = logging.getLogger(__name__)


NO_MEASURE = "livekit's load_fnc option no longer carries a measure of its own"


# A worker that starts again under the same name (a pod's container, after its node was reset)
# registers anew while LiveKit may hold the last process's dead socket for 15-20 minutes and offer
# it calls. This process's start, to the second, keeps the two apart; the roster keys by the
# worker's name, so the gateway offers only the newest registration.
STARTED = f"{time.time():.0f}"


A_SLOT_AT_LEAST = "a worker holds at least one call: PINECALL_MAX_JOBS={max_jobs}"


# The id livekit's AgentServer holds until LiveKit answers its registration with the real one.
UNREGISTERED = "unregistered"


# The exit a cordoned worker leaves with; its unit's RestartPreventExitStatus= keeps it down.
CORDONED_EXIT = 3


# What LiveKit reads, on LiveKit's scale: its server offers a job under REFUSED_AT and its
# framework declines at it, so a worker that counts its calls is at REFUSED_AT with every slot
# taken. The server reads the load the worker last reported (every 2.5 s): a burst faster than
# that, on a worker's last slots, is declined by the framework and offered to no one else.
class Load:
    """The load LiveKit reads: its line times the slots taken, or the machine's CPU share."""

    def __init__(
        self, max_jobs: int | None, measure: Callable[[AgentServer], float] | None = None
    ) -> None:
        """A worker nobody has dispatched to yet."""
        if max_jobs is not None and max_jobs < 1:
            raise DeclarationRefused(A_SLOT_AT_LEAST.format(max_jobs=max_jobs))
        self.max_jobs = max_jobs
        self.measure = measure or livekits_measure()
        self.refused = False

    def __call__(self, server: AgentServer) -> float:
        """The load now, as livekit reads it."""
        if self.max_jobs is None:
            return self.announced(self.measure(server))
        return self.at(len(server.active_jobs))

    def at(self, active: int) -> float:
        """The load of a worker holding this many calls, when its slots were measured."""
        return self.announced(REFUSED_AT * active / (self.max_jobs or 1))

    def announced(self, load: float) -> float:
        """The load, a crossing of LiveKit's line said once each way."""
        refused = load >= REFUSED_AT
        if refused != self.refused:
            self.refused = refused
            if refused:
                logger.warning("load %.2f: livekit routes no job here until it falls", load)
            else:
                logger.info("load %.2f: livekit routes jobs here again", load)
        return load


class Heartbeats:
    """The worker's report to its gateway every five seconds, and a cordon's way out."""

    def __init__(
        self, server: AgentServer, gateway: GatewayClient, settings: Settings, minute: LastMinute
    ) -> None:
        """Not beating yet; `leave` is set when the worker must go."""
        self.server = server
        self.gateway = gateway
        self.fleet = settings.fleet
        self.max_jobs = settings.max_jobs
        self.name = worker_name_of(settings)
        self.agent_name = agent_name_of(settings)
        self.minute = minute
        self.cordoned = False
        self.leave = asyncio.Event()
        # Set once LiveKit registered the worker and the gateway answered a heartbeat uncordoned.
        self.ready = asyncio.Event()

    def beat(self) -> HeartbeatRequest:
        """What this worker holds now, and what its calls did in the last minute."""
        active = len(self.server.active_jobs)
        last = self.minute.of(asyncio.get_running_loop().time())
        # The name only once LiveKit registered it: before, a call offered to it waits for nobody.
        registered = self.server.id != UNREGISTERED
        return HeartbeatRequest(
            fleet=self.fleet,
            worker=self.name,
            agent_name=self.agent_name if registered else None,
            active=active,
            max_jobs=self.max_jobs,
            load=self.gateways_load(active),
            draining=self.server.draining,
            ended=last.ended,
            failed=last.failed,
            errors=last.errors,
            turns=last.turns,
            first_audio_p95_s=last.first_audio_p95_s,
        )

    # The gateway's scale: calls over slots, full at 1.0 (roster.refused_at); a worker on CPU
    # reports the share LiveKit reads, which is the same number on both scales.
    def gateways_load(self, active: int) -> float:
        """The load the gateway reads for this worker."""
        if self.max_jobs is not None:
            return active / self.max_jobs
        load_of = self.server.load_fnc
        return load_of(self.server) if isinstance(load_of, Load) else 0.0

    # A gateway away is said and waited out: the worker keeps its calls meanwhile.
    async def run(self) -> None:
        """Beat until cordoned; then ask the worker to leave."""
        while True:
            try:
                existing = await self.gateway.heartbeat(self.beat())
            except GatewayRefused as refused:
                logger.warning("heartbeat: %s", refused)
            else:
                if existing.cordoned:
                    logger.warning("cordoned by the gateway: draining, then leaving")
                    self.cordoned = True
                    self.leave.set()
                    return
                if self.server.id != UNREGISTERED:
                    self.ready.set()
            await asyncio.sleep(HEARTBEAT_S)


# systemd's Type=notify: `systemctl restart` returns only once this is said, so a release starts
# the next instance only after this one can take calls. Outside systemd there is nobody to tell.
async def announced_ready(beats: Heartbeats, notify: str | None) -> bool:
    """Tell systemd the worker is ready once LiveKit registered it and the gateway heard it."""
    await beats.ready.wait()
    if not notify:
        return False
    # An address that starts with @ is in the abstract namespace, whose first byte is a zero.
    address = "\0" + notify[1:] if notify.startswith("@") else notify
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as systemd:
        systemd.connect(address)
        systemd.sendall(b"READY=1")
    logger.info("registered with LiveKit and heard by the gateway: ready")
    return True


# The job's process names its call's worker as the heartbeat does: the same settings, the same host.
def worker_name_of(settings: Settings) -> str:
    """What the worker is called in its heartbeats and its calls: its setting, or the short host."""
    return settings.worker_name or socket.gethostname().split(".")[0]


# Its own name, not the fleet's: LiveKit offers a job only to the workers registered under the
# name a dispatch carries, so the gateway reaches this process and no other (docs/scaling.md).
def agent_name_of(settings: Settings) -> str:
    """The name the worker registers under with LiveKit: its fleet's, its own, its start."""
    return f"{agent_name(settings.fleet, worker_name_of(settings))}.{STARTED}"


# livekit's own CPU average, read off its options' default rather than its private class.
def livekits_measure() -> Callable[[AgentServer], float]:
    """The load function livekit runs when given none: the machine's CPU over a few seconds."""
    (declared,) = (option for option in fields(ServerOptions) if option.name == "load_fnc")
    measure = declared.default
    if not callable(measure):
        raise DeclarationRefused(NO_MEASURE)

    def measured(server: AgentServer) -> float:
        load = measure(server)
        if not isinstance(load, int | float):
            raise DeclarationRefused(NO_MEASURE)
        return float(load)

    return measured
