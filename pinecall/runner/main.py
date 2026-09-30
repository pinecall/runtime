"""The runner: a world's hosted apps kept running as the gateway wants them, one container each."""

import asyncio
import hashlib
import io
import logging
import shutil
import signal
import socket
import tarfile
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from pinecall.domain.errors import GatewayRefused, SettingsRefused, UpstreamFailed
from pinecall.domain.names import PRODUCTION, Env
from pinecall.process.settings import Settings
from pinecall.runner import _podman as podman
from pinecall.runner._podman import Container, Done, Engine, Launch
from pinecall.wire.rest.hosting import (
    AppEnvironment,
    AppLogs,
    RunnerHeartbeatRequest,
    RunnerHeartbeatResponse,
    RunnerReport,
    WantedApp,
)

logger = logging.getLogger(__name__)


# Runs one podman verb; the tests hand their own.
type Ran = Callable[..., Awaitable[Done]]


BEAT_S = 5.0


# From `podman run` to the gateway seeing the release's agents: past it, the release failed.
REGISTERS_WITHIN_S = 120.0


# What a failure says of the process's own output: its last lines.
LAST_LINES = 20


# What a person asking for an app's logs is sent: its process's last lines.
SENT_LINES = 300


LONGEST_WHY = 2000


START = ("./node_modules/.bin/pinecall", "start")


# What infra/apps/fence.nft matches: the runner's bridges and no other of the machine.
BRIDGE_PREFIX = "pca"


NO_KEY = "PINECALL_RUNNER_KEY: a runner knocks its gateway with its world's runner key"


NOT_THE_RELEASE = "release {release}'s sources are not the ones uploaded: sha256 differs"


INSTALL_FAILED = "installing the dependencies failed:\n{output}"


START_FAILED = "podman could not start the release:\n{output}"


EXITED = "the process exited before its agents registered:\n{output}"


NEVER_REGISTERED = "no agent registered from this release within {seconds:.0f}s:\n{output}"


@dataclass
class Runner:
    """What the runner keeps between two beats: the reports to send and when each host started."""

    engine: Engine
    root: Path
    gateway: httpx.AsyncClient
    ran: Ran = podman.ran
    reports: list[RunnerReport] = field(default_factory=list[RunnerReport])
    logs: list[AppLogs] = field(default_factory=list[AppLogs])
    started_at: dict[str, float] = field(default_factory=dict[str, float])
    reported_live: set[str] = field(default_factory=set[str])

    async def beat(self, name: str) -> RunnerHeartbeatResponse:
        """Send what happened since the last beat; the gateway answers what should be running."""
        body = RunnerHeartbeatRequest(runner=name, reports=self.reports, logs=self.logs)
        answer = await self.gateway.post("/v1/runner/heartbeat", json=body.written())
        if not answer.is_success:
            raise GatewayRefused(answer.text, answered=answer.status_code)
        self.reports = []
        self.logs = []
        return RunnerHeartbeatResponse.model_validate(answer.json())

    async def reconcile(self, wanted: RunnerHeartbeatResponse, now: float) -> None:
        """Make the containers what the gateway wants: start, report, stop what is not wanted."""
        containers = await self._containers(wanted.world)
        by_name = {container.name: container for container in containers}
        keep: set[str] = set()
        for app in wanted.apps:
            ours = {each.name for each in containers if each.app == _label(app)}
            # The release serving now keeps serving until the one wanted registers.
            keep |= ours | {app.host}
            container = by_name.get(app.host)
            if app.failed:
                keep.discard(app.host)
            elif container is None:
                await self._started(app, wanted.world, now)
            elif not container.is_running:
                await self._failed(app, EXITED, container)
                keep.discard(app.host)
            elif app.registered:
                await self._live(app)
                keep -= ours - {app.host}
            elif now - self.started_at.setdefault(app.host, now) > REGISTERS_WITHIN_S:
                await self._failed(app, NEVER_REGISTERED, container)
                keep.discard(app.host)
        for container in containers:
            if container.name not in keep:
                await self._stopped(container.name)
        await self._read_the_logs_asked_for(wanted.apps, containers)

    async def _containers(self, world: Env) -> list[Container]:
        done = await self.ran(podman.listing_argv(world))
        if done.returncode != 0:
            raise UpstreamFailed(f"podman ps: {done.output.strip()}")
        return podman.containers_in(done.output)

    async def _started(self, app: WantedApp, world: Env, now: float) -> None:
        network = _network(app)
        folder = self.root / app.org / app.name / f"r{app.release}"
        try:
            await self.ran(podman.network_argv(network, _bridge(app)))
            if not (folder / "node_modules").is_dir():
                await self._installed(app, folder, network)
            environment = await self._environment(app)
        except UpstreamFailed as failure:
            self._report(app, "failed", str(failure))
            return
        command = [*START, "--prod"] if world == PRODUCTION else list(START)
        launch = Launch(
            world=world,
            app=_label(app),
            host=app.host,
            network=network,
            folder=folder,
            command=command,
        )
        argv = podman.run_argv(self.engine, launch, sorted(environment))
        done = await self.ran(argv, env=environment)
        if done.returncode != 0:
            self._report(app, "failed", START_FAILED.format(output=_tail(done.output)))
            return
        self.started_at[app.host] = now
        logger.info("started %s/%s release %d as %s", app.org, app.name, app.release, app.host)

    async def _installed(self, app: WantedApp, folder: Path, network: str) -> None:
        answer = await self.gateway.get(
            f"/v1/runner/apps/{app.org}/{app.name}/releases/{app.release}/source"
        )
        if not answer.is_success:
            raise UpstreamFailed(f"the release's sources: {answer.status_code} {answer.text}")
        if hashlib.sha256(answer.content).hexdigest() != app.sha256:
            raise UpstreamFailed(NOT_THE_RELEASE.format(release=app.release))
        await asyncio.to_thread(unpacked, answer.content, folder)
        scratch = folder.with_name(f"{folder.name}.scratch")
        scratch.mkdir(exist_ok=True)
        argv = podman.install_argv(self.engine, folder, network, scratch)
        try:
            done = await self.ran(argv, within_s=podman.INSTALL_WITHIN_S)
        finally:
            await asyncio.to_thread(shutil.rmtree, scratch, ignore_errors=True)
        if done.returncode != 0:
            shutil.rmtree(folder / "node_modules", ignore_errors=True)
            raise UpstreamFailed(INSTALL_FAILED.format(output=_tail(done.output)))

    async def _environment(self, app: WantedApp) -> Mapping[str, str]:
        answer = await self.gateway.get(f"/v1/runner/apps/{app.org}/{app.name}/environment")
        if not answer.is_success:
            raise UpstreamFailed(f"the app's environment: {answer.status_code} {answer.text}")
        return AppEnvironment.model_validate(answer.json()).environment

    async def _live(self, app: WantedApp) -> None:
        if app.host not in self.reported_live:
            self._report(app, "live", "")
            self.reported_live.add(app.host)

    async def _failed(self, app: WantedApp, why: str, container: Container) -> None:
        logs = await self.ran(podman.logs_argv(container.name, LAST_LINES))
        seconds = REGISTERS_WITHIN_S
        self._report(app, "failed", why.format(output=_tail(logs.output), seconds=seconds))

    # The release asked for, else the one still serving while it installs.
    async def _read_the_logs_asked_for(
        self, apps: list[WantedApp], containers: list[Container]
    ) -> None:
        for app in apps:
            ours = [each.name for each in containers if each.app == _label(app)]
            host = app.host if app.host in ours else next(iter(ours), None)
            if not app.logs_wanted or host is None:
                continue
            done = await self.ran(podman.logs_argv(host, SENT_LINES))
            self.logs.append(AppLogs(org=app.org, name=app.name, host=host, lines=done.output))

    async def _stopped(self, name: str) -> None:
        await self.ran(podman.stop_argv(name), within_s=podman.DRAIN_S + 15)
        await self.ran(podman.remove_argv(name))
        self.started_at.pop(name, None)
        logger.info("stopped %s", name)

    def _report(self, app: WantedApp, state: str, why: str) -> None:
        if state == "failed":
            logger.warning("%s/%s release %d failed: %s", app.org, app.name, app.release, why)
        self.reports.append(
            RunnerReport.model_validate(
                {
                    "org": app.org,
                    "name": app.name,
                    "host": app.host,
                    "state": state,
                    "why": why[-LONGEST_WHY:],
                }
            )
        )


# The gateway read the tarball before keeping it; read again here, where it lands on a disk.
def unpacked(source: bytes, folder: Path) -> None:
    """The release's sources in their own folder, nothing outside it."""
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(source), mode="r:gz") as tarball:
        tarball.extractall(folder, filter="data")


async def run(settings: Settings) -> int:
    """Keep the world's hosted apps running until told to stop; the apps go on without it."""
    if settings.runner_key is None:
        raise SettingsRefused(NO_KEY)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for each in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(each, stop.set)
    headers = {"Authorization": f"Bearer {settings.runner_key}"}
    async with httpx.AsyncClient(
        base_url=settings.gateway_url, headers=headers, timeout=30.0
    ) as gateway:
        runner = Runner(
            engine=Engine(image=settings.runner_image, runtime=settings.runner_runtime),
            root=Path(settings.runner_root),
            gateway=gateway,
        )
        name = socket.gethostname().split(".")[0]
        while not stop.is_set():
            try:
                await runner.reconcile(await runner.beat(name), time.time())
            except (UpstreamFailed, httpx.HTTPError):
                logger.warning("runner: this beat did nothing", exc_info=True)
            try:
                await asyncio.wait_for(stop.wait(), BEAT_S)
            except TimeoutError:
                continue
    return 0


def _label(app: WantedApp) -> str:
    return f"{app.org}/{app.name}"


def _network(app: WantedApp) -> str:
    return "pinecall-" + _stamp(app)


def _bridge(app: WantedApp) -> str:
    return BRIDGE_PREFIX + _stamp(app)


def _stamp(app: WantedApp) -> str:
    return hashlib.sha256(_label(app).encode()).hexdigest()[:12]


def _tail(output: str) -> str:
    return "\n".join(output.strip().splitlines()[-LAST_LINES:])
