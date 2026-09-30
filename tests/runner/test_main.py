"""Tests for the runner: a world's hosted apps started, reported, replaced and stopped."""

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest
from websockets.asyncio.client import ClientConnection

from pinecall.domain.person import THE_RUNNER
from pinecall.runner._podman import APP_LABEL, Done, Engine
from pinecall.runner.main import REGISTERS_WITHIN_S, Runner, unpacked
from tests.conftest import Knocking, issued, postgres, received_until, sent
from tests.tenancy.test_hosting import PROJECT, tarball

ENGINE = Engine(image="node:24-slim", runtime="runsc")


@dataclass
class Podman:
    """podman as the runner sees it: containers by name, and every verb it was asked."""

    containers: dict[str, dict[str, str]] = field(default_factory=dict[str, dict[str, str]])
    asked: list[list[str]] = field(default_factory=list[list[str]])
    environments: list[Mapping[str, str]] = field(default_factory=list[Mapping[str, str]])
    install_fails: bool = False

    async def __call__(
        self, argv: Sequence[str], *, within_s: float = 0, env: Mapping[str, str] | None = None
    ) -> Done:
        """The runner's `ran`, answered from the containers kept here."""
        del within_s
        self.asked.append(list(argv))
        verb = argv[1]
        if verb == "ps":
            rows = [
                {"Names": [name], "Labels": {APP_LABEL: row["app"]}, "State": row["state"]}
                for name, row in self.containers.items()
            ]
            return Done(0, json.dumps(rows))
        if verb == "run" and "--rm" in argv:
            if self.install_fails:
                return Done(1, "npm ERR! 404 not found")
            folder = next(part for part in argv if part.startswith("--volume=")).split("=")[1]
            (Path(folder.split(":")[0]) / "node_modules").mkdir()
            return Done(0, "added 60 packages")
        if verb == "run":
            name = next(part for part in argv if part.startswith("--name=")).split("=")[1]
            app = next(part for part in argv if part.startswith(f"--label={APP_LABEL}="))
            self.containers[name] = {"app": app.split("=", 2)[2], "state": "running"}
            self.environments.append(dict(env or {}))
            return Done(0, "a1b2c3")
        if verb == "rm":
            self.containers.pop(argv[-1], None)
        if verb == "logs":
            return Done(0, "pinecall: DeclarationRefused: voice is the world's now")
        return Done(0, "")

    def stopped(self) -> list[str]:
        """The containers told to stop, in order."""
        return [argv[-1] for argv in self.asked if argv[1] == "stop"]


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
        runner = Runner(engine=ENGINE, root=tmp_path, gateway=gateway, ran=podman)
        opened = World(knocking=knocking, runner=runner, podman=podman)
        yield opened
        for socket in opened.sockets:
            await socket.close()


async def upload(world: World) -> None:
    async with world.knocking.http(world.knocking.app["production"]) as http:
        answer = await http.post("/v1/hosted/support/releases", content=PROJECT)
    assert answer.status_code == 200, answer.text


async def tick(world: World, now: float = 1000.0) -> None:
    await world.runner.reconcile(await world.runner.beat("apps-1"), now)


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
    unpacked(tarball({"package.json": b"{}"}), folder)
    assert not (folder / "agents").exists()


@postgres
async def test_a_release_is_installed_and_started_in_production_with_its_environment(
    world: World,
) -> None:
    await upload(world)
    async with world.knocking.http(world.knocking.app["production"]) as http:
        await http.put("/v1/secrets/CRM_TOKEN", json={"value": "x"})
    await tick(world)
    [(host, row)] = world.podman.containers.items()
    assert host.startswith("support-r1-")
    assert row == {"app": f"{world.knocking.org.id}/support", "state": "running"}
    installs = [argv for argv in world.podman.asked if argv[1] == "run" and "--rm" in argv]
    [started] = [argv for argv in world.podman.asked if argv[1] == "run" and "--rm" not in argv]
    assert len(installs) == 1
    assert started[-3:] == ["./node_modules/.bin/pinecall", "start", "--prod"]
    [environment] = world.podman.environments
    assert set(environment) == {"CRM_TOKEN", "PINECALL_KEY", "PINECALL_URL"}
    assert not any("PINECALL_KEY=" in part for part in started)


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
