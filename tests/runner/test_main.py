"""Tests for the runner: a world's hosted apps started, reported, replaced and stopped."""

import asyncio
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest
from websockets.asyncio.client import ClientConnection

from pinecall.domain.person import THE_RUNNER
from pinecall.runner._plan import MOST_CRASHES, REGISTERS_WITHIN_S
from pinecall.runner._podman import APP_LABEL, RELEASE_LABEL, Done, Engine
from pinecall.runner.main import Runner, unpacked
from tests.conftest import Knocking, issued, postgres, received_until, sent
from tests.tenancy.test_hosting import PROJECT, tarball

ENGINE = Engine(image="node:24-slim", runtime="runsc")


@dataclass
class Podman:
    """podman as the runner sees it: containers by name, and every verb it was asked."""

    containers: dict[str, dict[str, str]] = field(default_factory=dict[str, dict[str, str]])
    asked: list[list[str]] = field(default_factory=list[list[str]])
    # What each container was started with, read off the file mounted for it, as it would.
    environments: list[Mapping[str, str]] = field(default_factory=list[Mapping[str, str]])
    install_fails: bool = False
    logs_read: list[str] = field(default_factory=list[str])
    clock: float = 1000.0

    async def __call__(self, argv: Sequence[str], *, within_s: float = 0) -> Done:
        """The runner's `ran`, answered from the containers kept here."""
        del within_s
        self.asked.append(list(argv))
        verb = argv[1]
        if verb == "ps":
            world = next(part for part in argv if "pinecall.world=" in part).split("=")[-1]
            rows = [
                {
                    "Names": [name],
                    "Labels": {APP_LABEL: row["app"], RELEASE_LABEL: row["release"]},
                    "State": row["state"],
                    "StartedAt": float(row["started_at"]),
                }
                for name, row in self.containers.items()
                if row["world"] == world
            ]
            return Done(0, json.dumps(rows))
        if verb == "run" and "--rm" in argv:
            if self.install_fails:
                return Done(1, "npm ERR! 404 not found")
            folder = next(part for part in argv if part.endswith(":/app:U")).split("=")[1]
            (Path(folder.split(":")[0]) / "node_modules").mkdir()
            return Done(0, "added 60 packages")
        if verb == "run":
            name = next(part for part in argv if part.startswith("--name=")).split("=")[1]
            labels = {
                part.split("=", 2)[1]: part.split("=", 2)[2]
                for part in argv
                if part.startswith("--label=")
            }
            mounted = next(part for part in argv if part.endswith(":/run/pinecall:ro"))
            self.containers[name] = {
                "app": labels[APP_LABEL],
                "release": labels[RELEASE_LABEL],
                "state": "running",
                "world": labels["pinecall.world"],
                "started_at": str(self.clock),
            }
            self.environments.append(read_off(Path(mounted.split("=", 1)[1].split(":")[0])))
            return Done(0, "a1b2c3")
        if verb == "rm":
            self.containers.pop(argv[-1], None)
        if verb == "logs":
            self.logs_read.append(argv[-1])
            return Done(0, "pinecall: DeclarationRefused: voice is the world's now")
        return Done(0, "")

    def stopped(self) -> list[str]:
        """The containers told to stop, in order."""
        return [argv[-1] for argv in self.asked if argv[1] == "stop"]

    def exited(self, name: str) -> None:
        """The container's process died."""
        self.containers[name]["state"] = "exited"


def read_off(environment: Path) -> dict[str, str]:
    """The environment as the container's shell reads it off the file: `export NAME='value'`."""
    read: dict[str, str] = {}
    for line in (environment / "env").read_text().splitlines():
        name, quoted = line.removeprefix("export ").split("=", 1)
        read[name] = quoted[1:-1].replace("'\\''", "'")
    assert oct((environment / "env").stat().st_mode & 0o777) == "0o444"
    return read


@dataclass
class World:
    """The gateway, a runner of its production, and the app sockets a test opened."""

    knocking: Knocking
    runner: Runner
    podman: Podman
    sockets: list[ClientConnection] = field(default_factory=list[ClientConnection])


@pytest.fixture
async def world(knocking: Knocking, tmp_path: Path) -> AsyncIterator[World]:
    pool = knocking.gateway.connections.pool
    key = await issued(pool, "default", "production", frozenset({THE_RUNNER}))
    podman = Podman()
    headers = {"Authorization": f"Bearer {key}"}
    async with httpx.AsyncClient(base_url=knocking.url, headers=headers) as gateway:
        runner = Runner(
            engine=ENGINE,
            root=tmp_path / "releases",
            environments=tmp_path / "run",
            gateway=gateway,
            ran=podman,
        )
        opened = World(knocking=knocking, runner=runner, podman=podman)
        yield opened
        for socket in opened.sockets:
            await socket.close()


async def upload(world: World) -> None:
    async with world.knocking.http(world.knocking.app["production"]) as http:
        answer = await http.post("/v1/hosted/support/releases", content=PROJECT)
    assert answer.status_code == 200, answer.text


# A beat, and every step it started done: the tests read what a whole beat leaves.
async def tick(world: World, now: float = 1000.0) -> None:
    world.podman.clock = now
    await world.runner.reconcile(await world.runner.beat("apps-1"), now)
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


def test_a_release_is_unpacked_into_its_own_folder_whole(tmp_path: Path) -> None:
    folder = tmp_path / "r1"
    unpacked(tarball({"package.json": b"{}", "agents/a/agent.ts": b"x"}), folder)
    assert (folder / "agents/a/agent.ts").read_bytes() == b"x"
    assert oct((folder / "agents/a/agent.ts").stat().st_mode & 0o777) == "0o644"
    assert oct((folder / "agents").stat().st_mode & 0o777) == "0o755"
    unpacked(tarball({"package.json": b"{}"}), folder)
    assert not (folder / "agents").exists()


@postgres
async def test_a_release_is_installed_and_started_in_production_with_its_environment(
    world: World,
) -> None:
    await upload(world)
    async with world.knocking.http(world.knocking.app["production"]) as http:
        await http.put("/v1/secrets/CRM_URL", json={"value": "https://crm.example"})
    await tick(world)
    [(host, row)] = world.podman.containers.items()
    assert host.startswith("support-r1-")
    assert row == {
        "app": f"{world.knocking.org.id}/support",
        "release": "1",
        "state": "running",
        "world": "production",
        "started_at": "1000.0",
    }
    installs = [argv for argv in world.podman.asked if argv[1] == "run" and "--rm" in argv]
    [started] = [argv for argv in world.podman.asked if argv[1] == "run" and "--rm" not in argv]
    assert len(installs) == 1
    assert started[-3:] == ["./node_modules/.bin/pinecall", "start", "--prod"]
    [environment] = world.podman.environments
    assert set(environment) == {"CRM_URL", "PINECALL_KEY", "PINECALL_URL"}
    assert environment["CRM_URL"] == "https://crm.example"
    assert not any("PINECALL_KEY" in part for part in started)
    assert (world.runner.root / world.knocking.org.id / "support" / "r1.installed").is_file()


@postgres
async def test_a_release_that_registers_is_reported_live(world: World) -> None:
    await upload(world)
    await tick(world)
    [host] = world.podman.containers
    await registered_from(world, host)
    await tick(world)
    await tick(world)
    assert (await serving(world))["live_release"] == 1


@postgres
async def test_a_new_release_replaces_the_old_only_once_it_registers(world: World) -> None:
    await upload(world)
    await tick(world)
    [old] = world.podman.containers
    await registered_from(world, old)
    await tick(world)
    await upload(world)
    await tick(world)
    assert len(world.podman.containers) == 2
    assert world.podman.stopped() == []
    new = next(name for name in world.podman.containers if name != old)
    await registered_from(world, new)
    await tick(world)
    assert world.podman.stopped() == [old]
    assert list(world.podman.containers) == [new]
    assert not (world.runner.environments / old).exists()
    await tick(world)
    assert sorted(
        path.name for path in (world.runner.root / world.knocking.org.id / "support").iterdir()
    ) == ["r2", "r2.installed"]


@postgres
async def test_an_install_that_fails_is_reported_and_the_old_release_keeps_serving(
    world: World,
) -> None:
    await upload(world)
    await tick(world)
    [old] = world.podman.containers
    await registered_from(world, old)
    await tick(world)
    world.podman.install_fails = True
    await upload(world)
    await tick(world)
    await tick(world)
    app = await serving(world)
    assert app["release"] == 2
    assert "npm ERR! 404" in str(app["failed_why"])
    assert list(world.podman.containers) == [old]


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
    assert "voice is the world's now" in str(app["failed_why"])
    assert world.podman.containers == {}


@postgres
async def test_an_app_dropped_has_its_container_stopped(world: World) -> None:
    await upload(world)
    await tick(world)
    [host] = world.podman.containers
    async with world.knocking.http(world.knocking.app["production"]) as http:
        await http.delete("/v1/hosted/support")
    await tick(world)
    assert world.podman.stopped() == [host]
    assert world.podman.containers == {}


# Found on the first machine with two: the sandbox's runner stopped production's apps.
@postgres
async def test_a_runner_of_another_world_on_the_same_podman_leaves_this_worlds_apps_alone(
    world: World,
) -> None:
    await upload(world)
    await tick(world)
    [host] = world.podman.containers
    pool = world.knocking.gateway.connections.pool
    key = await issued(pool, "default", "sandbox", frozenset({THE_RUNNER}))
    headers = {"Authorization": f"Bearer {key}"}
    async with httpx.AsyncClient(base_url=world.knocking.url, headers=headers) as gateway:
        sandbox = Runner(
            engine=ENGINE,
            root=world.runner.root,
            environments=world.runner.environments,
            gateway=gateway,
            ran=world.podman,
        )
        await sandbox.reconcile(await sandbox.beat("apps-1"), 1000.0)
    assert list(world.podman.containers) == [host]
    assert world.podman.stopped() == []


@postgres
async def test_logs_asked_for_are_read_off_the_container_and_sent_on_the_next_beat(
    world: World,
) -> None:
    await upload(world)
    await tick(world)
    [host] = world.podman.containers
    async with world.knocking.http(world.knocking.app["production"]) as http:
        await http.get("/v1/hosted/support/logs")
    await tick(world)
    await tick(world)
    async with world.knocking.http(world.knocking.app["production"]) as http:
        logs = (await http.get("/v1/hosted/support/logs")).json()
    assert world.podman.logs_read[-1] == host
    assert (logs["host"], logs["lines"]) == (
        host,
        "pinecall: DeclarationRefused: voice is the world's now",
    )


@postgres
async def test_a_live_host_that_exits_is_run_again_with_its_last_lines_kept(world: World) -> None:
    await upload(world)
    await tick(world)
    [host] = world.podman.containers
    await registered_from(world, host)
    await tick(world)
    world.podman.exited(host)
    await tick(world)
    assert world.podman.containers[host]["state"] == "running"
    assert world.podman.stopped() == []
    assert world.runner.crashes[host] == [1000.0]
    await tick(world)
    async with world.knocking.http(world.knocking.app["production"]) as http:
        logs = (await http.get("/v1/hosted/support/logs")).json()
    assert "voice is the world's now" in logs["lines"]
    assert (await serving(world))["live_release"] == 1


@postgres
async def test_a_host_that_keeps_exiting_is_given_up_and_reported(world: World) -> None:
    await upload(world)
    await tick(world)
    [host] = world.podman.containers
    await registered_from(world, host)
    await tick(world)
    for each in range(MOST_CRASHES):
        world.podman.exited(host)
        await tick(world, now=1000.0 + each)
    world.podman.exited(host)
    await tick(world, now=1000.0 + MOST_CRASHES)
    await tick(world, now=1000.0 + MOST_CRASHES + 1)
    app = await serving(world)
    assert f"exited {MOST_CRASHES} times" in str(app["failed_why"])
    assert world.podman.containers == {}
    assert world.podman.stopped() == [host]


@postgres
async def test_a_stopped_app_has_its_container_stopped_and_started_again_on_start(
    world: World,
) -> None:
    await upload(world)
    await tick(world)
    [host] = world.podman.containers
    async with world.knocking.http(world.knocking.app["production"]) as http:
        await http.post("/v1/hosted/support/stop")
        await tick(world)
        assert world.podman.stopped() == [host]
        await http.post("/v1/hosted/support/start")
    await tick(world)
    assert list(world.podman.containers) == [host]
    runs = [argv for argv in world.podman.asked if argv[1] == "run" and "--rm" not in argv]
    assert len(runs) == 2
