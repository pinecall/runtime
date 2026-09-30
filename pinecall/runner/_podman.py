"""The container engine as the runner drives it: podman, one argv per verb, run and awaited."""

import asyncio
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from pinecall.domain.errors import UpstreamFailed

# Every container the runner starts carries these, so a runner that restarts finds what is its
# own. Two runners, one per world, may share a machine and its podman: each sees its world's.
APP_LABEL = "pinecall.app"


WORLD_LABEL = "pinecall.world"


RELEASE_LABEL = "pinecall.release"


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


# Where a container finds what it is started with: a file the runner wrote, mounted read-only.
ENVIRONMENT = "/run/pinecall"


A_VARIABLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


IN_THE_NAME = re.compile(r"-r(?P<release>[0-9]+)-")


NO_ANSWER = "podman {verb}: no answer in {seconds:.0f}s"


NOT_A_VARIABLE = "{name!r} is not an environment variable's name"


# The environment podman itself runs with, and nothing else: where it finds its binaries and
# its storage. Nothing of an org's is ever in it.
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
    """A container the runner started: its name (the host), its app and release, how it is."""

    name: str
    app: str
    release: int
    state: str
    started_at: float

    @property
    def is_running(self) -> bool:
        """Whether its process is up."""
        return self.state == "running"


@dataclass(frozen=True)
class Launch:
    """One host to start: whose, which release, its network, its sources, what it starts with."""

    world: str
    app: str
    host: str
    release: int
    network: str
    folder: Path
    environment: Path
    command: Sequence[str]


class _Row(BaseModel):
    """One row of podman's listing, the fields read of it."""

    model_config = ConfigDict(extra="ignore")

    names: list[str] = Field(alias="Names")
    labels: dict[str, str] | None = Field(alias="Labels")
    state: str = Field(alias="State")
    started_at: float = Field(default=0.0, alias="StartedAt")


# The process's own shell reads its environment, then becomes the command.
READ_THEN_RUN = f'. {ENVIRONMENT}/env && exec "$@"'


_LISTING: TypeAdapter[list[_Row]] = TypeAdapter(list[_Row])


# An org's secret never rides this process's environment: a name like LD_PRELOAD or PATH there
# would be read by podman itself, which runs as root.
async def ran(argv: Sequence[str], *, within_s: float = A_VERB_WITHIN_S) -> Done:
    """Run one podman verb to its end; UpstreamFailed when it takes longer than it may."""
    own = {name: value for name in PODMANS_OWN if (value := os.environ.get(name)) is not None}
    process = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=own
    )
    try:
        printed, _ = await asyncio.wait_for(process.communicate(), within_s)
    except TimeoutError:
        raise UpstreamFailed(NO_ANSWER.format(verb=argv[1], seconds=within_s)) from None
    finally:
        # Past its time, or the runner leaving: the verb does not outlive whoever waited for it.
        if process.returncode is None:
            process.kill()
            await process.wait()
    return Done(returncode=process.returncode or 0, output=printed.decode(errors="replace"))


# Single-quoted, so any value is itself: a key of several lines, a quote, a dollar sign.
def exported(environment: Mapping[str, str]) -> str:
    """The environment as a shell reads it: one `export NAME='value'` a variable."""
    lines: list[str] = []
    for name, value in sorted(environment.items()):
        if not A_VARIABLE.match(name):
            raise UpstreamFailed(NOT_A_VARIABLE.format(name=name))
        quoted = value.replace("'", "'\\''")
        lines.append(f"export {name}='{quoted}'")
    return "\n".join(lines) + "\n"


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
# pnpm's binary and its store land in HOME: a folder on disk the runner deletes after, not tmpfs.
def install_argv(engine: Engine, folder: Path, network: str, scratch: Path) -> list[str]:
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
        "--env=HOME=/scratch",
        f"--tmpfs={TMP}",
        f"--volume={scratch}:/scratch:U",
        f"--volume={folder}:/app:U",
        "--workdir=/app",
        engine.image,
        "sh",
        "-c",
        install,
    ]


def run_argv(engine: Engine, launch: Launch) -> list[str]:
    """Start one host: read-only, capped, on its own network, its environment read off a file."""
    return [
        "podman",
        "run",
        "--detach",
        f"--name={launch.host}",
        f"--hostname={launch.host}",
        f"--label={APP_LABEL}={launch.app}",
        f"--label={WORLD_LABEL}={launch.world}",
        f"--label={RELEASE_LABEL}={launch.release}",
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
        f"--volume={launch.environment}:{ENVIRONMENT}:ro",
        f"--volume={launch.folder}:/app:ro",
        "--workdir=/app",
        engine.image,
        "sh",
        "-c",
        READ_THEN_RUN,
        "sh",
        *launch.command,
    ]


# The bridge's name is what the fence matches: only the runner's bridges, never another podman
# network of the machine. A Linux interface name is 15 characters at most. No DNS of podman's: it
# answers on the host, which the fence closes, so a container asks the public resolvers itself.
def network_argv(network: str, bridge: str, world: str) -> list[str]:
    """One app's own network: two apps never share a bridge, so they never reach each other."""
    return [
        "podman",
        "network",
        "create",
        "--ignore",
        "--disable-dns",
        f"--label={WORLD_LABEL}={world}",
        f"--interface-name={bridge}",
        network,
    ]


def prune_networks_argv(world: str) -> list[str]:
    """Every network of the world no container is on, gone: an app dropped leaves none behind."""
    return ["podman", "network", "prune", "--force", f"--filter=label={WORLD_LABEL}={world}"]


def listing_argv(world: str) -> list[str]:
    """Every container this world's runner started, running or not; never another world's."""
    return [
        "podman",
        "ps",
        "--all",
        f"--filter=label={APP_LABEL}",
        f"--filter=label={WORLD_LABEL}={world}",
        "--format=json",
    ]


def stop_argv(name: str) -> list[str]:
    """SIGTERM, and the drain's time before the kill."""
    return ["podman", "stop", f"--time={DRAIN_S}", name]


def remove_argv(name: str) -> list[str]:
    """The container gone, its logs with it."""
    return ["podman", "rm", "--force", name]


def logs_argv(name: str, lines: int) -> list[str]:
    """The last lines a container printed."""
    return ["podman", "logs", f"--tail={lines}", name]


# A container started before containers carried their release is still this runner's to stop:
# its release is read off its name, as the gateway wrote it there.
def containers_in(listing: str) -> list[Container]:
    """The containers `podman ps --format=json` printed."""
    found: list[Container] = []
    for row in _LISTING.validate_json(listing or "[]"):
        labels = row.labels or {}
        named = IN_THE_NAME.search(row.names[0]) if row.names else None
        release = labels.get(RELEASE_LABEL) or (named["release"] if named else "0")
        if row.names:
            found.append(
                Container(
                    name=row.names[0],
                    app=labels.get(APP_LABEL, ""),
                    release=int(release),
                    state=row.state,
                    started_at=row.started_at,
                )
            )
    return found
