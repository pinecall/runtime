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
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from pinecall.domain.errors import GatewayRefused, SettingsRefused, UpstreamFailed
from pinecall.domain.names import PRODUCTION, Env
from pinecall.process.settings import Settings
from pinecall.runner import _podman as podman
from pinecall.runner._plan import (
    Failed,
    Revive,
    Start,
    Step,
    Stop,
    WentLive,
    crashes_within,
    label_of,
    planned,
)
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


# Installs run an org's own scripts, a gigabyte each: this many at once on one machine.
INSTALLS_AT_ONCE = 2


# What a failure says of the process's own output: its last lines.
LAST_LINES = 20


# What a person asking for an app's logs is sent: its process's last lines.
SENT_LINES = 300


LONGEST_WHY = 2000


# Every install is marked done beside its folder, never inside it: what the org packed is its own.
INSTALLED = ".installed"


START = ("./node_modules/.bin/pinecall", "start")


# What infra/apps/fence.nft matches: the runner's bridges and no other of the machine.
BRIDGE_PREFIX = "pca"


NO_KEY = "PINECALL_RUNNER_KEY: a runner knocks its gateway with its world's runner key"


NOT_THE_RELEASE = "release {release}'s sources are not the ones uploaded: sha256 differs"


INSTALL_FAILED = "installing the dependencies failed:\n{output}"


START_FAILED = "podman could not start the release:\n{output}"


@dataclass
class Runner:
    """A world's runner: what it holds between two beats, and the steps it has in hand."""

    engine: Engine
    root: Path
    # A tmpfs: an org's secrets are written here for its container, and nowhere on a disk.
    environments: Path
    gateway: httpx.AsyncClient
    ran: Ran = podman.ran
    reports: list[RunnerReport] = field(default_factory=list[RunnerReport])
    logs: list[AppLogs] = field(default_factory=list[AppLogs])
    # When each host's process exited and was run again, so one that keeps exiting is given up.
    crashes: dict[str, list[float]] = field(default_factory=dict[str, list[float]])
    # One task per app with steps under way; an app with one is left alone this beat.
    working: dict[str, asyncio.Task[None]] = field(default_factory=dict[str, asyncio.Task[None]])
    installing: asyncio.Semaphore = field(
        default_factory=lambda: asyncio.Semaphore(INSTALLS_AT_ONCE)
    )

    async def beat(self, name: str) -> RunnerHeartbeatResponse:
        """Send what happened since the last beat; the gateway answers what should be running."""
        reports, self.reports = self.reports, []
        logs, self.logs = self.logs, []
        body = RunnerHeartbeatRequest(runner=name, reports=reports, logs=logs)
        try:
            answer = await self.gateway.post("/v1/runner/heartbeat", json=body.written())
        except httpx.HTTPError:
            self.reports, self.logs = reports + self.reports, logs + self.logs
            raise
        if not answer.is_success:
            self.reports, self.logs = reports + self.reports, logs + self.logs
            raise GatewayRefused(answer.text, answered=answer.status_code)
        return RunnerHeartbeatResponse.model_validate(answer.json())

    async def reconcile(self, wanted: RunnerHeartbeatResponse, now: float) -> None:
        """Make the containers what the gateway wants; the long steps go on past this beat."""
        # Finished first: a step that ends after the listing would be planned again on a stale one.
        self._finished()
        containers = await self._containers(wanted.world)
        for label, steps in planned(wanted.apps, containers, self.crashes, now).items():
            if label not in self.working:
                self.working[label] = asyncio.create_task(self._done(wanted.world, steps, now))
        await self._logs_asked_for(wanted.apps, containers)
        await self._swept(wanted, containers)

    async def close(self) -> None:
        """Leave: every step under way is cut short; the containers go on without the runner."""
        for task in self.working.values():
            task.cancel()
        await asyncio.gather(*self.working.values(), return_exceptions=True)
        self.working.clear()

    async def _containers(self, world: Env) -> list[Container]:
        done = await self.ran(podman.listing_argv(world))
        if done.returncode != 0:
            raise UpstreamFailed(f"podman ps: {done.output.strip()}")
        return podman.containers_in(done.output)

    def _finished(self) -> None:
        for label, task in list(self.working.items()):
            if task.done():
                del self.working[label]
                if not task.cancelled() and task.exception() is not None:
                    logger.error("%s: its steps failed", label, exc_info=task.exception())

    async def _done(self, world: Env, steps: Sequence[Step], now: float) -> None:
        for step in steps:
            match step:
                case Start(app):
                    await self._started(world, app, app.host, app.release)
                case Revive(app, container):
                    await self._revived(world, app, container, now)
                case Stop(container):
                    await self._stopped(container.name)
                case WentLive(app):
                    self._report(app, "live", "")
                case Failed(app, why, container):
                    logs = await self.ran(podman.logs_argv(container.name, LAST_LINES))
                    self._report(app, "failed", why.format(output=_tail(logs.output)))

    async def _started(self, world: Env, app: WantedApp, host: str, release: int) -> None:
        label = label_of(app)
        network = _network(world, label)
        folder = self.root / app.org / app.name / f"r{release}"
        try:
            await self.ran(podman.network_argv(network, _bridge(world, label), world))
            if not folder.with_name(folder.name + INSTALLED).is_file():
                await self._installed(app, folder, network)
            environment = self.environments / host
            await asyncio.to_thread(_written, environment, await self._environment(app))
        except UpstreamFailed as failure:
            self._report(app, "failed", str(failure))
            return
        launch = Launch(
            world=world,
            app=label,
            host=host,
            release=release,
            network=network,
            folder=folder,
            environment=environment,
            command=[*START, "--prod"] if world == PRODUCTION else list(START),
        )
        done = await self.ran(podman.run_argv(self.engine, launch))
        if done.returncode != 0:
            self._report(app, "failed", START_FAILED.format(output=_tail(done.output)))
            return
        logger.info("started %s release %d as %s", label, release, host)

    # Its last lines are kept as the app's logs before the container goes, so the exit is read.
    async def _revived(self, world: Env, app: WantedApp, container: Container, now: float) -> None:
        logs = await self.ran(podman.logs_argv(container.name, SENT_LINES))
        self.logs.append(
            AppLogs(org=app.org, name=app.name, host=container.name, lines=logs.output)
        )
        logger.warning(
            "%s: %s exited, run again:\n%s", label_of(app), container.name, _tail(logs.output)
        )
        self.crashes[container.name] = [
            *crashes_within(self.crashes.get(container.name, []), now),
            now,
        ]
        await self.ran(podman.remove_argv(container.name))
        await self._started(world, app, container.name, container.release)

    async def _installed(self, app: WantedApp, folder: Path, network: str) -> None:
        answer = await self.gateway.get(
            f"/v1/runner/apps/{app.org}/{app.name}/releases/{app.release}/source"
        )
        if not answer.is_success:
            raise UpstreamFailed(f"the release's sources: {answer.status_code} {answer.text}")
        if hashlib.sha256(answer.content).hexdigest() != app.sha256:
            raise UpstreamFailed(NOT_THE_RELEASE.format(release=app.release))
        await asyncio.to_thread(unpacked, answer.content, folder)
        scratch = folder.with_name(folder.name + ".scratch")
        scratch.mkdir(exist_ok=True)
        try:
            async with self.installing:
                argv = podman.install_argv(self.engine, folder, network, scratch)
                done = await self.ran(argv, within_s=podman.INSTALL_WITHIN_S)
        finally:
            await asyncio.to_thread(shutil.rmtree, scratch, ignore_errors=True)
        if done.returncode != 0:
            raise UpstreamFailed(INSTALL_FAILED.format(output=_tail(done.output)))
        folder.with_name(folder.name + INSTALLED).touch()

    async def _environment(self, app: WantedApp) -> Mapping[str, str]:
        answer = await self.gateway.get(f"/v1/runner/apps/{app.org}/{app.name}/environment")
        if not answer.is_success:
            raise UpstreamFailed(f"the app's environment: {answer.status_code} {answer.text}")
        return AppEnvironment.model_validate(answer.json()).environment

    # The host asked for, else the one still serving while it installs.
    async def _logs_asked_for(
        self, apps: Sequence[WantedApp], containers: Sequence[Container]
    ) -> None:
        for app in apps:
            ours = [each.name for each in containers if each.app == label_of(app)]
            host = app.host if app.host in ours else next(iter(ours), None)
            if not app.logs_wanted or host is None:
                continue
            done = await self.ran(podman.logs_argv(host, SENT_LINES))
            self.logs.append(AppLogs(org=app.org, name=app.name, host=host, lines=done.output))

    async def _stopped(self, name: str) -> None:
        await self.ran(podman.stop_argv(name), within_s=podman.DRAIN_S + 15)
        await self.ran(podman.remove_argv(name))
        await asyncio.to_thread(shutil.rmtree, self.environments / name, ignore_errors=True)
        logger.info("stopped %s", name)

    # What no container and no wanted release uses any more: a release's folder, a host's
    # environment, and, when nothing is under way that could be making one, a network.
    async def _swept(
        self, wanted: RunnerHeartbeatResponse, containers: Sequence[Container]
    ) -> None:
        releases = {(app.org, app.name, app.release) for app in wanted.apps}
        releases |= {_release_of(each) for each in containers}
        hosts = {each.name for each in containers} | {app.host for app in wanted.apps}
        await asyncio.to_thread(_swept_releases, self.root, releases)
        await asyncio.to_thread(_swept_environments, self.environments, hosts)
        if not self.working:
            await self.ran(podman.prune_networks_argv(wanted.world))

    def _report(self, app: WantedApp, state: str, why: str) -> None:
        if state == "failed":
            logger.warning("%s release %d failed: %s", label_of(app), app.release, why)
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
# Every file is left readable: the container runs as another user than the one that installs.
def unpacked(source: bytes, folder: Path) -> None:
    """The release's sources in their own folder, nothing outside it."""
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(source), mode="r:gz") as tarball:
        tarball.extractall(folder, filter="data")
    for path in folder.rglob("*"):
        mode = path.stat().st_mode
        path.chmod((mode | 0o444 | (0o111 if path.is_dir() or mode & 0o100 else 0)) & 0o7777)


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
            environments=Path(settings.runner_environments),
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
        await runner.close()
    return 0


def _network(world: str, label: str) -> str:
    return "pinecall-" + _stamp(world, label)


def _bridge(world: str, label: str) -> str:
    return BRIDGE_PREFIX + _stamp(world, label)


def _stamp(world: str, label: str) -> str:
    return hashlib.sha256(f"{world}/{label}".encode()).hexdigest()[:12]


def _tail(output: str) -> str:
    return "\n".join(output.strip().splitlines()[-LAST_LINES:])


# Root-owned, world-readable: the container's own user reads it through the mount, and no other
# user of the machine reaches the folder above it.
def _written(environment: Path, values: Mapping[str, str]) -> None:
    shutil.rmtree(environment, ignore_errors=True)
    environment.mkdir(parents=True, mode=0o755)
    (environment / "env").write_text(podman.exported(values))
    (environment / "env").chmod(0o444)


def _release_of(container: Container) -> tuple[str, str, int]:
    org, name = container.app.split("/", 1)
    return org, name, container.release


# A release's folder, its marker and its scratch go together, once nothing runs or wants it.
def _swept_releases(root: Path, in_use: set[tuple[str, str, int]]) -> None:
    for path in sorted(root.glob("*/*/r*")) if root.is_dir() else []:
        stem = path.name.removesuffix(INSTALLED).removesuffix(".scratch")
        release = int(stem[1:]) if stem[1:].isdigit() else -1
        if (path.parent.parent.name, path.parent.name, release) not in in_use:
            _gone(path)


def _swept_environments(root: Path, hosts: set[str]) -> None:
    for path in sorted(root.iterdir()) if root.is_dir() else []:
        if path.name not in hosts:
            _gone(path)


def _gone(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)
