"""The container engine as the runner drives it: podman, one argv per verb, run and awaited."""

import asyncio
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from pinecall.domain.errors import UpstreamFailed

# Every container the runner starts carries these, so a restart finds what is its own.
APP_LABEL = "pinecall.app"


HOST_LABEL = "pinecall.host"


# The spike's measure (72 MB an idle agent under gVisor) with room for a tool's burst.
MEMORY = "256m"


CPUS = "0.5"


PIDS = "256"


TMP = "/tmp:size=64m"


# The install runs the org's own scripts: the same sandbox, more room, and a clock.
INSTALL_MEMORY = "1g"


INSTALL_WITHIN_S = 300.0


# Public resolvers: the box's own (the cloud's metadata address) is behind the fence.
RESOLVERS = ("1.1.1.1", "8.8.8.8")


# What a drain needs: ten seconds for the gateway, thirty for the slowest tool.
DRAIN_S = 45


A_VERB_WITHIN_S = 60.0


NO_ANSWER = "podman {verb}: no answer in {seconds:.0f}s"


# The environment podman itself runs with: where it finds its binaries and its storage.
PODMANS_OWN = ("PATH", "HOME", "XDG_RUNTIME_DIR", "CONTAINERS_CONF", "CONTAINERS_STORAGE_CONF")


@dataclass(frozen=True)
class Done:
    """What one podman verb ended with: its exit, and what it printed, both streams together."""

    returncode: int
    output: str


@dataclass(frozen=True)
class Engine:
    """Which image every app runs in, and under which OCI runtime."""

    image: str
    runtime: str


@dataclass(frozen=True)
class Container:
    """A container the runner started: its name (the host), the app it serves, and its state."""

    name: str
    app: str
    state: str

    @property
    def is_running(self) -> bool:
        """Whether its process is up."""
        return self.state == "running"


@dataclass(frozen=True)
class Launch:
    """One release to start: the app's label, its host, its network, its folder, its command."""

    app: str
    host: str
    network: str
    folder: Path
    command: Sequence[str]


class _Row(BaseModel):
    """One row of podman's listing, the three fields read of it."""

    model_config = ConfigDict(extra="ignore")

    names: list[str] = Field(alias="Names")
    labels: dict[str, str] | None = Field(alias="Labels")
    state: str = Field(alias="State")


_LISTING: TypeAdapter[list[_Row]] = TypeAdapter(list[_Row])


async def ran(
    argv: Sequence[str], *, within_s: float = A_VERB_WITHIN_S, env: Mapping[str, str] | None = None
) -> Done:
    """Run one podman verb to its end; UpstreamFailed when it takes longer than it may."""
    environment = {
        name: value for name in PODMANS_OWN if (value := os.environ.get(name)) is not None
    }
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env={**environment, **(env or {})},
    )
    try:
        printed, _ = await asyncio.wait_for(process.communicate(), within_s)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise UpstreamFailed(NO_ANSWER.format(verb=argv[1], seconds=within_s)) from None
    return Done(returncode=process.returncode or 0, output=printed.decode(errors="replace"))


def sandboxed(engine: Engine) -> list[str]:
    """What every container of an org's code runs under: its runtime, no privilege, no root."""
    return [
        f"--runtime={engine.runtime}",
        "--userns=auto",
        "--cap-drop=all",
        "--security-opt=no-new-privileges",
        *(f"--dns={resolver}" for resolver in RESOLVERS),
    ]


# The lockfile says the package manager; without one, npm resolves what package.json names.
def install_argv(engine: Engine, folder: Path, network: str) -> list[str]:
    """Install the release's dependencies inside the sandbox, into its own folder."""
    if (folder / "pnpm-lock.yaml").is_file():
        install = "corepack pnpm install --frozen-lockfile --prod"
    elif (folder / "package-lock.json").is_file():
        install = "npm ci --omit=dev --no-audit --no-fund"
    else:
        install = "npm install --omit=dev --no-audit --no-fund"
    return [
        "podman",
        "run",
        "--rm",
        *sandboxed(engine),
        f"--network={network}",
        f"--memory={INSTALL_MEMORY}",
        f"--memory-swap={INSTALL_MEMORY}",
        f"--pids-limit={PIDS}",
        "--env=HOME=/tmp",
        f"--tmpfs={TMP}",
        f"--volume={folder}:/app:U",
        "--workdir=/app",
        engine.image,
        "sh",
        "-c",
        install,
    ]


# The values ride podman's own environment, named here and never written to a file or an argv.
def run_argv(engine: Engine, launch: Launch, names: Sequence[str]) -> list[str]:
    """Start one release: read-only, capped, on its own network, under its host's name."""
    return [
        "podman",
        "run",
        "--detach",
        f"--name={launch.host}",
        f"--hostname={launch.host}",
        f"--label={APP_LABEL}={launch.app}",
        f"--label={HOST_LABEL}={launch.host}",
        *sandboxed(engine),
        "--read-only",
        f"--network={launch.network}",
        f"--memory={MEMORY}",
        f"--memory-swap={MEMORY}",
        f"--cpus={CPUS}",
        f"--pids-limit={PIDS}",
        f"--tmpfs={TMP}",
        "--env=HOME=/tmp",
        "--env=NO_COLOR=1",
        *(f"--env={name}" for name in names),
        f"--volume={launch.folder}:/app:ro",
        "--workdir=/app",
        engine.image,
        *launch.command,
    ]


# The bridge's name is what the fence matches: only the runner's bridges, never another podman
# network of the machine. A Linux interface name is 15 characters at most.
def network_argv(network: str, bridge: str) -> list[str]:
    """One app's own network: two apps never share a bridge, so they never reach each other."""
    return ["podman", "network", "create", "--ignore", f"--interface-name={bridge}", network]


def listing_argv() -> list[str]:
    """Every container the runner started, running or not."""
    return ["podman", "ps", "--all", f"--filter=label={APP_LABEL}", "--format=json"]


def stop_argv(name: str) -> list[str]:
    """SIGTERM, and the drain's time before the kill."""
    return ["podman", "stop", f"--time={DRAIN_S}", name]


def remove_argv(name: str) -> list[str]:
    """The container gone, its logs with it."""
    return ["podman", "rm", "--force", name]


def logs_argv(name: str, lines: int) -> list[str]:
    """The last lines a container printed."""
    return ["podman", "logs", f"--tail={lines}", name]


def containers_in(listing: str) -> list[Container]:
    """The containers `podman ps --format=json` printed."""
    rows = _LISTING.validate_json(listing or "[]")
    return [
        Container(name=row.names[0], app=(row.labels or {}).get(APP_LABEL, ""), state=row.state)
        for row in rows
        if row.names
    ]
