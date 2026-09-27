"""Beside a worker's calls: the load livekit reads, the heartbeat, the recorder, the traces."""

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import fields
from pathlib import Path

from livekit import api
from livekit.agents import AgentServer
from livekit.agents.telemetry import set_tracer_provider
from livekit.agents.worker import ServerOptions
from livekit.protocol import egress
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from pinecall.domain.errors import DeclarationRefused, GatewayRefused
from pinecall.domain.settings import Settings
from pinecall.fleet.gateway_client import Gateway
from pinecall.fleet.hub import HEARTBEAT_S, REFUSED_AT
from pinecall.wire.rest import Heartbeat

logger = logging.getLogger(__name__)

# ── the load livekit reads ──

NO_MEASURE = "livekit's load_fnc option no longer carries a measure of its own"
A_SLOT_AT_LEAST = "a worker holds at least one call: PINECALL_MAX_JOBS={max_jobs}"


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


# livekit reads the load every half second: two jobs inside one reading see the same count, so
# max_jobs is set one under what was measured.
class Load:
    """The load a worker reports: calls over its measured slots, or its machine's CPU."""

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
            return self.said(self.measure(server))
        return self.at(len(server.active_jobs))

    def at(self, active: int) -> float:
        """The load of a worker holding this many calls, when its slots were measured."""
        return self.said(active / (self.max_jobs or 1))

    def said(self, load: float) -> float:
        """The load, a crossing of livekit's line said once each way."""
        refused = load >= REFUSED_AT
        if refused != self.refused:
            self.refused = refused
            if refused:
                logger.warning("load %.2f: livekit routes no job here until it falls", load)
            else:
                logger.info("load %.2f: livekit routes jobs here again", load)
        return load


# ── the heartbeat ──

# The exit a cordoned worker leaves with; its unit's RestartPreventExitStatus= keeps it down.
CORDONED_EXIT = 3


class Heartbeats:
    """The worker's report to its gateway every five seconds, and a cordon's way out."""

    def __init__(
        self, server: AgentServer, gateway: Gateway, settings: Settings, name: str
    ) -> None:
        """Not beating yet; `leave` is set when the worker must go."""
        self.server = server
        self.gateway = gateway
        self.fleet = settings.fleet
        self.max_jobs = settings.max_jobs
        self.name = name
        self.cordoned = False
        self.leave = asyncio.Event()

    def beat(self) -> Heartbeat:
        """What this worker holds now."""
        load_of = self.server.load_fnc
        return Heartbeat(
            fleet=self.fleet,
            worker=self.name,
            active=len(self.server.active_jobs),
            max_jobs=self.max_jobs,
            load=load_of(self.server) if isinstance(load_of, Load) else 0.0,
            draining=self.server.draining,
        )

    # A gateway away is said and waited out: the worker keeps its calls meanwhile.
    async def run(self) -> None:
        """Beat until cordoned; then ask the worker to leave."""
        while True:
            try:
                standing = await self.gateway.heartbeat(self.beat())
            except GatewayRefused as refused:
                logger.warning("heartbeat: %s", refused)
            else:
                if standing.cordoned:
                    logger.warning("cordoned by the gateway: draining, then leaving")
                    self.cordoned = True
                    self.leave.set()
                    return
            await asyncio.sleep(HEARTBEAT_S)


# ── the recorder ──

# Audio only with no layout runs on livekit's SDK, not Chromium. The default mix, never
# DUAL_CHANNEL_AGENT: it drops the agent's second track, and the melody records as silence.
THE_WHOLE_ROOM = egress.AudioMixing.DEFAULT_MIXING
AUDIO_FILE = "audio.ogg"
# egress finishes writing after the call: the summary must not point at a file not yet there.
THE_FILE_MAY_TAKE_S = 8.0
FILE_ASKED_EVERY_S = 0.2
# The recorder writes as a uid nobody names in advance: the directory is setgid, so its file
# stays the group's the gateway reads as.
SHARED_WITH_THE_RECORDER = 0o2770


def recording_path(root: Path, call: str) -> Path:
    """The call's audio file, in a directory of its own the recorder may write to."""
    directory = root / call
    directory.mkdir(parents=True, exist_ok=True)
    try:
        directory.chmod(SHARED_WITH_THE_RECORDER)
    except OSError as refused:
        logger.warning(
            "%s is not group-writable (%s): the recorder cannot write in it",
            directory,
            refused.strerror,
        )
    return directory / AUDIO_FILE


# A recorder that refuses leaves the call without audio, and the call goes on.
async def record_room(server: api.LiveKitAPI, room: str, audio: Path) -> str | None:
    """Start recording the room into the file; the recording's id, or None if it was refused."""
    try:
        started = await server.egress.start_room_composite_egress(
            egress.RoomCompositeEgressRequest(
                room_name=room,
                audio_only=True,
                audio_mixing=THE_WHOLE_ROOM,
                file_outputs=[
                    egress.EncodedFileOutput(
                        file_type=egress.EncodedFileType.OGG,
                        filepath=str(audio),
                        disable_manifest=True,
                    )
                ],
            )
        )
    except api.TwirpError:
        logger.warning("this call is not recorded: the recorder refused", exc_info=True)
        return None
    return started.egress_id


async def file_written(server: api.LiveKitAPI, recording: str, audio: Path) -> bool:
    """Stop the recording and wait for its file; whether it was written in time."""
    try:
        await server.egress.stop_egress(egress.StopEgressRequest(egress_id=recording))
    except api.TwirpError:
        logger.warning("the recorder had stopped already for %s", recording, exc_info=True)
    # egress says nothing when the file lands: the disk is asked, a few times a second.
    deadline = time.monotonic() + THE_FILE_MAY_TAKE_S
    while not await asyncio.to_thread(_written, audio):
        if time.monotonic() > deadline:
            logger.warning(
                "the recording %s was not written within %gs", recording, THE_FILE_MAY_TAKE_S
            )
            return False
        await asyncio.sleep(FILE_ASKED_EVERY_S)
    return True


def _written(audio: Path) -> bool:
    return audio.is_file() and audio.stat().st_size > 0


# ── traces ──

SERVICE = "pinecall-worker"
FLEET_ATTRIBUTE = "pinecall.fleet"
NOT_A_HEADER = "an OTLP header is name=value, comma separated; {said!r} is not"


# livekit's tracer does nothing until handed a provider; once per job process, since the
# provider owns a thread.
def traced_to(settings: Settings) -> bool:
    """Send the process's spans where PINECALL_OTLP_ENDPOINT says; False when it says nowhere."""
    if settings.otlp_endpoint is None:
        return False
    provider = TracerProvider(resource=Resource.create({SERVICE_NAME: SERVICE}))
    exporter = OTLPSpanExporter(
        endpoint=settings.otlp_endpoint, headers=otlp_headers(settings.otlp_headers)
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
    set_tracer_provider(
        provider, metadata={FLEET_ATTRIBUTE: settings.fleet}, allow_pii=settings.otlp_pii
    )
    return True


def otlp_headers(said: str | None) -> dict[str, str]:
    """`name=value,name=value`, as OTEL_EXPORTER_OTLP_HEADERS spells them."""
    headers: dict[str, str] = {}
    for pair in (said or "").split(","):
        if not pair.strip():
            continue
        name, equals, value = pair.partition("=")
        if not equals or not name.strip():
            raise DeclarationRefused(NOT_A_HEADER.format(said=pair))
        headers[name.strip()] = value.strip()
    return headers
