"""Tests for the runner: a world's hosted apps started, reported, replaced and stopped."""

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from websockets.asyncio.client import ClientConnection

from pinecall.domain.names import Json, JsonObject
from pinecall.domain.person import THE_RUNNER
from pinecall.runner._kube import APP_ANNOTATION, WORLD_LABEL, Cluster, Engine
from pinecall.runner._plan import MOST_CRASHES, REGISTERS_WITHIN_S
from pinecall.runner.main import Runner, sources_app
from tests.conftest import Knocking, issued, postgres, received_until, sent
from tests.tenancy.test_hosting import PROJECT

ENGINE = Engine(
    image="node:24-slim",
    runtime_class="gvisor",
    namespace="pinecall-apps",
    sources_url="http://runner-production.pinecall-runner.svc:8080",
)

THE_EXIT = "pinecall: DeclarationRefused: voice is the world's now"

NPM_FAILED = "npm ERR! 404 not found"


@dataclass
class Api:
    """The cluster's API as the runner sees it: pods and secrets by name, and every delete."""

    pods: dict[str, JsonObject] = field(default_factory=dict[str, JsonObject])
    secrets: dict[str, JsonObject] = field(default_factory=dict[str, JsonObject])
    stopped: list[str] = field(default_factory=list[str])
    logs_read: list[str] = field(default_factory=list[str])
    install_fails: bool = False
    clock: float = 1000.0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """The runner's knock, answered from what is kept here."""
        assert request.headers["authorization"] == "Bearer the-pods-own-token"
        kind, _, name = request.url.path.split("/namespaces/pinecall-apps/")[1].partition("/")
        if kind == "pods" and name.endswith("/log"):
            return self._log(name.removesuffix("/log"), request.url.params["container"])
        if request.method == "GET":
            world = request.url.params["labelSelector"].split(f"{WORLD_LABEL}=")[1]
            items = [pod for pod in self.pods.values() if _world_of(pod) == world]
            return httpx.Response(200, json={"items": items})
        if request.method == "POST":
            return self._created(kind, json.loads(request.content))
        grace = json.loads(request.content or b"{}").get("gracePeriodSeconds", 0)
        kept = self.pods if kind == "pods" else self.secrets
        if name not in kept:
            return httpx.Response(404, json={"reason": "NotFound"})
        del kept[name]
        if kind == "pods" and grace:
            self.stopped.append(name)
        return httpx.Response(200, json={})

    def exited(self, name: str) -> None:
        """The pod's process died."""
        self.pods[name]["status"] = {"phase": "Failed"}

    def environment_of(self, host: str) -> dict[str, str]:
        """The environment as the container's shell reads it off its secret."""
        read: dict[str, str] = {}
        string_data = self.secrets[f"{host}-env"]["stringData"]
        assert isinstance(string_data, dict)
        for line in str(string_data["env"]).splitlines():
            named, quoted = line.removeprefix("export ").split("=", 1)
            read[named] = quoted[1:-1].replace("'\\''", "'")
        return read

    def _created(self, kind: str, body: JsonObject) -> httpx.Response:
        metadata = body["metadata"]
        assert isinstance(metadata, dict)
        name = str(metadata["name"])
        if kind == "secrets":
            self.secrets[name] = body
            return httpx.Response(201, json=body)
        at = datetime.fromtimestamp(self.clock, UTC).isoformat()
        metadata["creationTimestamp"] = at
        running: Json = {"name": "app", "state": {"running": {"startedAt": at}}}
        body["status"] = (
            {"phase": "Failed"}
            if self.install_fails
            else {"phase": "Running", "containerStatuses": [running]}
        )
        self.pods[name] = body
        return httpx.Response(201, json=body)

    def _log(self, name: str, container: str) -> httpx.Response:
        self.logs_read.append(name)
        if container == "install":
            return httpx.Response(200, text=NPM_FAILED if self.install_fails else "")
        return httpx.Response(200, text="" if self.install_fails else THE_EXIT)


@dataclass
class World:
    """The gateway, a runner of its production, the cluster's API, and the app sockets opened."""

    knocking: Knocking
    runner: Runner
    api: Api
    token: Path
    sockets: list[ClientConnection] = field(default_factory=list[ClientConnection])


@pytest.fixture
async def world(knocking: Knocking, tmp_path: Path) -> AsyncIterator[World]:
    pool = knocking.gateway.connections.pool
    key = await issued(pool, "default", "production", frozenset({THE_RUNNER}))
    api = Api()
    token = tmp_path / "token"
    token.write_text("the-pods-own-token\n")
    headers = {"Authorization": f"Bearer {key}"}
    async with (
        httpx.AsyncClient(base_url=knocking.url, headers=headers) as gateway,
        httpx.AsyncClient(base_url="https://cluster", transport=httpx.MockTransport(api)) as kube,
    ):
        runner = Runner(cluster=Cluster(http=kube, engine=ENGINE, token=token), gateway=gateway)
        opened = World(knocking=knocking, runner=runner, api=api, token=token)
        yield opened
        for socket in opened.sockets:
            await socket.close()


async def upload(world: World) -> None:
    async with world.knocking.http(world.knocking.app["production"]) as http:
        answer = await http.post("/v1/hosted/support/releases", content=PROJECT)
    assert answer.status_code == 200, answer.text


# A beat, and every step it started done: the tests read what a whole beat leaves.
async def tick(world: World, now: float = 1000.0) -> None:
    world.api.clock = now
    await world.runner.reconcile(await world.runner.beat("runner-1"), now)
    await asyncio.gather(*world.runner.working.values(), return_exceptions=True)


async def serving(world: World) -> dict[str, object]:
    async with world.knocking.http(world.knocking.app["production"]) as http:
        [app] = (await http.get("/v1/hosted")).json()["apps"]
    return app


async def registered_from(world: World, host: str) -> None:
    """An app socket of the org, saying it runs on the host, holding the agent."""
    socket = await world.knocking.socket("/v1/apps", world.knocking.app["production"])
    world.sockets.append(socket)
    await sent(socket, "agent.register", {"routes": [], "host": host})
    await received_until(socket, "agent.registered")


def pod_named(world: World, host: str) -> JsonObject:
    return world.api.pods[host]


@postgres
async def test_a_release_is_started_in_production_with_its_environment_and_its_sources(
    world: World,
) -> None:
    await upload(world)
    async with world.knocking.http(world.knocking.app["production"]) as http:
        await http.put("/v1/secrets/CRM_URL", json={"value": "https://crm.example"})
    await tick(world)
    [host] = world.api.pods
    assert host.startswith("support-r1-")
    pod = json.dumps(pod_named(world, host))
    assert '"/opt/pinecall/bin/pinecall", "start", "--prod"' in pod
    assert f'"{APP_ANNOTATION}": "{world.knocking.org.id}/support"' in pod
    assert "PINECALL_KEY" not in pod
    environment = world.api.environment_of(host)
    assert set(environment) == {"CRM_URL", "PINECALL_KEY", "PINECALL_URL"}
    assert environment["CRM_URL"] == "https://crm.example"
    [digest] = world.runner.sources
    assert f"{ENGINE.sources_url}/sources/{digest}" in pod
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=sources_app(world.runner)), base_url="http://runner"
    ) as fetching:
        fetched = await fetching.get(f"/sources/{digest}")
        unknown = await fetching.get("/sources/" + "0" * 64)
    assert fetched.content == PROJECT
    assert unknown.status_code == 404


@postgres
async def test_a_release_that_registers_is_reported_live(world: World) -> None:
    await upload(world)
    await tick(world)
    [host] = world.api.pods
    await registered_from(world, host)
    await tick(world)
    await tick(world)
    assert (await serving(world))["live_release"] == 1


@postgres
async def test_a_new_release_replaces_the_old_only_once_it_registers(world: World) -> None:
    await upload(world)
    await tick(world)
    [old] = world.api.pods
    await registered_from(world, old)
    await tick(world)
    await upload(world)
    await tick(world)
    assert len(world.api.pods) == 2
    assert world.api.stopped == []
    new = next(name for name in world.api.pods if name != old)
    await registered_from(world, new)
    await tick(world)
    assert world.api.stopped == [old]
    assert list(world.api.pods) == [new]
    assert list(world.api.secrets) == [f"{new}-env"]
    await tick(world)
    assert len(world.runner.sources) == 1


@postgres
async def test_an_install_that_fails_is_reported_and_the_old_release_keeps_serving(
    world: World,
) -> None:
    await upload(world)
    await tick(world)
    [old] = world.api.pods
    await registered_from(world, old)
    await tick(world)
    world.api.install_fails = True
    await upload(world)
    # Started, seen failed, reported: the install fails in its pod, read on the beat after.
    await tick(world)
    await tick(world)
    await tick(world)
    app = await serving(world)
    assert app["release"] == 2
    assert NPM_FAILED in str(app["failed_why"])
    assert list(world.api.pods) == [old]


@postgres
async def test_a_release_that_never_registers_is_stopped_and_reported_with_its_last_lines(
    world: World,
) -> None:
    await upload(world)
    await tick(world, now=1000.0)
    await tick(world, now=1000.0 + REGISTERS_WITHIN_S + 1)
    await tick(world, now=1000.0 + REGISTERS_WITHIN_S + 2)
    app = await serving(world)
    assert "no agent registered" in str(app["failed_why"])
    assert THE_EXIT in str(app["failed_why"])
    assert world.api.pods == {}


@postgres
async def test_an_app_dropped_has_its_pod_stopped_and_its_sources_forgotten(
    world: World,
) -> None:
    await upload(world)
    await tick(world)
    [host] = world.api.pods
    async with world.knocking.http(world.knocking.app["production"]) as http:
        await http.delete("/v1/hosted/support")
    await tick(world)
    assert world.api.stopped == [host]
    assert (world.api.pods, world.api.secrets) == ({}, {})
    await tick(world)
    assert world.runner.sources == {}


@postgres
async def test_a_runner_of_another_world_leaves_this_worlds_apps_alone(world: World) -> None:
    await upload(world)
    await tick(world)
    [host] = world.api.pods
    pool = world.knocking.gateway.connections.pool
    key = await issued(pool, "default", "sandbox", frozenset({THE_RUNNER}))
    headers = {"Authorization": f"Bearer {key}"}
    async with httpx.AsyncClient(base_url=world.knocking.url, headers=headers) as gateway:
        sandbox = Runner(cluster=world.runner.cluster, gateway=gateway)
        await sandbox.reconcile(await sandbox.beat("runner-2"), 1000.0)
    assert list(world.api.pods) == [host]
    assert world.api.stopped == []


@postgres
async def test_logs_asked_for_are_read_off_the_pod_and_sent_on_the_next_beat(
    world: World,
) -> None:
    await upload(world)
    await tick(world)
    [host] = world.api.pods
    async with world.knocking.http(world.knocking.app["production"]) as http:
        await http.get("/v1/hosted/support/logs")
    await tick(world)
    await tick(world)
    async with world.knocking.http(world.knocking.app["production"]) as http:
        logs = (await http.get("/v1/hosted/support/logs")).json()
    assert world.api.logs_read[-1] == host
    assert (logs["host"], logs["lines"]) == (host, THE_EXIT)


@postgres
async def test_a_live_host_that_exits_is_run_again_with_its_last_lines_kept(world: World) -> None:
    await upload(world)
    await tick(world)
    [host] = world.api.pods
    await registered_from(world, host)
    await tick(world)
    world.api.exited(host)
    await tick(world)
    status = pod_named(world, host)["status"]
    assert isinstance(status, dict)
    assert status["phase"] == "Running"
    assert world.api.stopped == []
    assert world.runner.crashes[host] == [1000.0]
    await tick(world)
    async with world.knocking.http(world.knocking.app["production"]) as http:
        logs = (await http.get("/v1/hosted/support/logs")).json()
    assert THE_EXIT in logs["lines"]
    assert (await serving(world))["live_release"] == 1


@postgres
async def test_a_host_that_keeps_exiting_is_given_up_and_reported(world: World) -> None:
    await upload(world)
    await tick(world)
    [host] = world.api.pods
    await registered_from(world, host)
    await tick(world)
    for each in range(MOST_CRASHES):
        world.api.exited(host)
        await tick(world, now=1000.0 + each)
    world.api.exited(host)
    await tick(world, now=1000.0 + MOST_CRASHES)
    await tick(world, now=1000.0 + MOST_CRASHES + 1)
    app = await serving(world)
    assert f"exited {MOST_CRASHES} times" in str(app["failed_why"])
    assert world.api.pods == {}
    assert world.api.stopped == [host]


@postgres
async def test_a_stopped_app_has_its_pod_stopped_and_started_again_on_start(
    world: World,
) -> None:
    await upload(world)
    await tick(world)
    [host] = world.api.pods
    async with world.knocking.http(world.knocking.app["production"]) as http:
        await http.post("/v1/hosted/support/stop")
        await tick(world)
        assert world.api.stopped == [host]
        await http.post("/v1/hosted/support/start")
    await tick(world)
    assert list(world.api.pods) == [host]


def _world_of(pod: JsonObject) -> str:
    metadata = pod["metadata"]
    assert isinstance(metadata, dict)
    labels = metadata["labels"]
    assert isinstance(labels, dict)
    return str(labels[WORLD_LABEL])
