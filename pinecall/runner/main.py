"""The runner: a world's hosted apps kept running as the gateway wants them, one pod each."""

import asyncio
import hashlib
import logging
import signal
import socket
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from pinecall.domain.errors import GatewayRefused, SettingsRefused, UpstreamFailed
from pinecall.domain.names import PRODUCTION, Env
from pinecall.process.settings import Settings
from pinecall.runner._kube import Cluster, Container, Engine, Launch, in_cluster
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
from pinecall.wire.rest.hosting import (
    AppEnvironment,
    AppLogs,
    RunnerHeartbeatRequest,
    RunnerHeartbeatResponse,
    RunnerReport,
    WantedApp,
)

logger = logging.getLogger(__name__)


BEAT_S = 5.0


# What a failure says of the process's own output: its last lines.
LAST_LINES = 20


# What a person asking for an app's logs is sent: its process's last lines.
SENT_LINES = 300


LONGEST_WHY = 2000


START = ("./node_modules/.bin/pinecall", "start")


NO_KEY = "PINECALL_RUNNER_KEY: a runner knocks its gateway with its world's runner key"


NOT_THE_RELEASE = "release {release}'s sources are not the ones uploaded: sha256 differs"


START_FAILED = "the cluster could not start the release:\n{output}"


# A release is (org, name, release): what the gateway numbers and what a pod carries.
type Release = tuple[str, str, int]


@dataclass
class Runner:
    """A world's runner: what it holds between two beats, and the steps it has in hand."""

    cluster: Cluster
    gateway: httpx.AsyncClient
    reports: list[RunnerReport] = field(default_factory=list[RunnerReport])
    logs: list[AppLogs] = field(default_factory=list[AppLogs])
    # When each host's process exited and was run again, so one that keeps exiting is given up.
    crashes: dict[str, list[float]] = field(default_factory=dict[str, list[float]])
    # One task per app with steps under way; an app with one is left alone this beat.
    working: dict[str, asyncio.Task[None]] = field(default_factory=dict[str, asyncio.Task[None]])
    # The releases a pod of this world may fetch, by their digest: read off the gateway, checked.
    sources: dict[str, bytes] = field(default_factory=dict[str, bytes])
    digests: dict[Release, str] = field(default_factory=dict[Release, str])

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
        """Make the pods what the gateway wants; the long steps go on past this beat."""
        # Finished first: a step that ends after the listing would be planned again on a stale one.
        self._finished()
        containers = await self.cluster.pods(wanted.world)
        for label, steps in planned(wanted.apps, containers, self.crashes, now).items():
            if label not in self.working:
                self.working[label] = asyncio.create_task(self._done(wanted.world, steps, now))
        await self._logs_asked_for(wanted.apps, containers)
        self._swept(wanted.apps, containers)

    async def close(self) -> None:
        """Leave: every step under way is cut short; the pods go on without the runner."""
        for task in self.working.values():
            task.cancel()
        await asyncio.gather(*self.working.values(), return_exceptions=True)
        self.working.clear()

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
                    await self.cluster.stop(container.name)
                    logger.info("stopped %s", container.name)
                case WentLive(app):
                    self._report(app, "live", "")
                case Failed(app, why, container):
                    output = await self.cluster.logs(container.name, LAST_LINES)
                    self._report(app, "failed", why.format(output=_tail(output)))

    async def _started(self, world: Env, app: WantedApp, host: str, release: int) -> None:
        try:
            digest = await self._fetched(app, release)
            environment = await self._environment(app)
        except UpstreamFailed as failure:
            self._report(app, "failed", str(failure))
            return
        launch = Launch(
            world=world,
            app=label_of(app),
            host=host,
            release=release,
            sha256=digest,
            command=[*START, "--prod"] if world == PRODUCTION else list(START),
        )
        try:
            await self.cluster.start(launch, environment)
        except UpstreamFailed as failure:
            self._report(app, "failed", START_FAILED.format(output=str(failure)))
            return
        logger.info("started %s release %d as %s", label_of(app), release, host)

    # Its last lines are kept as the app's logs before the pod goes, so the exit is read.
    async def _revived(self, world: Env, app: WantedApp, container: Container, now: float) -> None:
        output = await self.cluster.logs(container.name, SENT_LINES)
        self.logs.append(AppLogs(org=app.org, name=app.name, host=container.name, lines=output))
        logger.warning(
            "%s: %s exited, run again:\n%s", label_of(app), container.name, _tail(output)
        )
        self.crashes[container.name] = [
            *crashes_within(self.crashes.get(container.name, []), now),
            now,
        ]
        await self.cluster.remove(container.name)
        await self._started(world, app, container.name, container.release)

    # The wanted release is checked against the digest the gateway kept when it was uploaded; an
    # older one, revived, is the one the gateway still holds under its number.
    async def _fetched(self, app: WantedApp, release: int) -> str:
        known = self.digests.get((app.org, app.name, release))
        if known is not None and known in self.sources:
            return known
        answer = await self.gateway.get(
            f"/v1/runner/apps/{app.org}/{app.name}/releases/{release}/source"
        )
        if not answer.is_success:
            raise UpstreamFailed(f"the release's sources: {answer.status_code} {answer.text}")
        digest = hashlib.sha256(answer.content).hexdigest()
        if release == app.release and digest != app.sha256:
            raise UpstreamFailed(NOT_THE_RELEASE.format(release=release))
        self.sources[digest] = answer.content
        self.digests[(app.org, app.name, release)] = digest
        return digest

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
            output = await self.cluster.logs(host, SENT_LINES)
            self.logs.append(AppLogs(org=app.org, name=app.name, host=host, lines=output))

    # A release no pod runs and nobody wants is no longer anybody's to fetch.
    def _swept(self, apps: Sequence[WantedApp], containers: Sequence[Container]) -> None:
        in_use: set[Release] = {(app.org, app.name, app.release) for app in apps}
        for each in containers:
            org, _, name = each.app.partition("/")
            in_use.add((org, name, each.release))
        for release in [each for each in self.digests if each not in in_use]:
            del self.digests[release]
        kept = set(self.digests.values())
        for digest in [each for each in self.sources if each not in kept]:
            del self.sources[digest]

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


def sources_app(runner: Runner) -> Starlette:
    """The one door a pod of this world knocks: its release's sources, by their digest."""

    async def served(request: Request) -> Response:
        found = runner.sources.get(str(request.path_params["sha256"]))
        if found is None:
            return Response(status_code=404)
        return Response(found, media_type="application/gzip")

    return Starlette(routes=[Route("/sources/{sha256}", served)])


async def run(settings: Settings) -> int:
    """Keep the world's hosted apps running until told to stop; the apps go on without it."""
    if settings.runner_key is None:
        raise SettingsRefused(NO_KEY)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for each in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(each, stop.set)
    engine = Engine(
        image=settings.runner_image,
        runtime_class=settings.runner_runtime_class,
        namespace=settings.runner_namespace,
        sources_url=settings.runner_sources_url,
    )
    headers = {"Authorization": f"Bearer {settings.runner_key}"}
    async with (
        httpx.AsyncClient(base_url=settings.gateway_url, headers=headers, timeout=30.0) as gateway,
        in_cluster(engine).http as kube,
    ):
        runner = Runner(cluster=Cluster(http=kube, engine=engine), gateway=gateway)
        server = uvicorn.Server(
            uvicorn.Config(
                sources_app(runner),
                host=settings.runner_listen,
                port=settings.runner_port,
                log_level="warning",
            )
        )
        serving = asyncio.create_task(server.serve())
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
        server.should_exit = True
        await serving
    return 0


def _tail(output: str) -> str:
    return "\n".join(output.strip().splitlines()[-LAST_LINES:])
