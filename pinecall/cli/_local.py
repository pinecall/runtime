"""`local`: the runtime whole on one machine: the services in Docker, the gateway, a worker."""

import argparse
import asyncio
import base64
import os
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from importlib.resources import files
from pathlib import Path
from shutil import which
from typing import IO

from pinecall.cli import _operator
from pinecall.domain.errors import PinecallError
from pinecall.domain.org import DEFAULT_ORG
from pinecall.domain.person import THE_FLEET
from pinecall.postgres.migrate import apply_migrations
from pinecall.process.settings import Settings
from pinecall.tenancy import keys as key_table

type Verb = Callable[[Settings, argparse.Namespace], int]


# Where one machine keeps its local runtime: the compose files, the database's name, the secrets.
DEFAULT_DIR = Path.home() / ".pinecall-runtime" / "local"


# What the package ships for Docker, written into that directory as they are here.
SHIPPED = (
    "compose.yaml",
    "livekit.yaml",
    "sip.yaml",
    "postgres/Dockerfile",
    "postgres/extensions.sql",
)


SERVICES = ("postgres", "redis", "livekit")


GATEWAY_URL = "http://127.0.0.1:8080"


NO_DOCKER = (
    "Docker is not here: install Docker Desktop (macOS, Windows) or Docker Engine with the compose "
    "plugin, start it, and run this again"
)


COMPOSE_FAILED = "docker compose could not bring the services up:\n{output}"


# Docker says a container is healthy before the host's side of its port answers: a moment, on a Mac.
PORTS_WITHIN_S = 60.0


NOT_ANSWERING = (
    "{service} did not answer on 127.0.0.1:{port} within {seconds:.0f} s: `docker compose ps`"
)


# The ports the compose publishes on loopback, as the settings name them.
PUBLISHED = {"Postgres": 55433, "Redis": 56380, "LiveKit": 7880}


# The settings the gateway and the worker run on here: the three services the compose publishes
# on loopback, a development LiveKit pair that exists only on this machine, and secrets drawn once.
SETTINGS = """DATABASE_URL=postgresql://pinecall:pinecall@127.0.0.1:55433/pinecall
PINECALL_REDIS_URL=redis://127.0.0.1:56380/0
LIVEKIT_URL=ws://127.0.0.1:7880
LIVEKIT_API_KEY=devkey
LIVEKIT_API_SECRET=a-laptop-only-livekit-secret-0123456789
PINECALL_GATEWAY_URL={gateway}
PINECALL_RECORDINGS={recordings}
PINECALL_FLEET=pinecall-sandbox
PINECALL_MAX_JOBS=4
PINECALL_WORKER_HTTP_PORT=8182
PINECALL_VAULT_KEY={vault}
PINECALL_OPS_KEY=pc_ops_{ops}
PINECALL_TOKEN_KEY={token}
"""


UP = """local · Postgres :55433 · Redis :56380 · LiveKit :7880 · settings in {env}
console   {gateway}/sandbox/
"""


FIRST = """  the first time, in another terminal:
    pinecall-runtime local init --email you@example.com --person "Your Name"
"""


RUNNING = (
    "gateway and a sandbox worker running; Ctrl-C stops them, the services stay (local down)\n"
)


DOWN = "local: the services stopped; the database and {env} are kept\n"


WIPED = "local: the services stopped and the database deleted; {env} is kept\n"


def local_group(group: argparse.ArgumentParser) -> None:
    """`local up | down | init | env`, every one on the directory `--dir` names."""
    group.add_argument("--dir", default=str(DEFAULT_DIR), metavar="<path>")
    verbs = group.add_subparsers(required=True)
    up = verbs.add_parser("up", help="the services, the schema, the gateway and a worker")
    up.add_argument(
        "--services-only", action="store_true", help="the services and the schema alone"
    )
    up.set_defaults(run=local_up)
    down = verbs.add_parser("down", help="the services stopped; the database kept")
    down.add_argument("--volumes", action="store_true", help="delete the database too")
    down.set_defaults(run=local_down)
    first = verbs.add_parser("init", help="the first org and person, on the local gateway")
    _operator.init_group(first)
    first.set_defaults(run=_on_the_local_settings(first.get_default("run")))
    verbs.add_parser(
        "env", help="the line that loads the local settings into a shell"
    ).set_defaults(run=local_env)


def local_up(_settings: Settings, args: argparse.Namespace) -> int:
    """Bring the services up, migrate, draw the secrets once, then run the gateway and a worker."""
    where = Path(args.dir)
    compose = written(where)
    if which("docker") is None:
        raise PinecallError(NO_DOCKER)
    up = subprocess.run(
        ["docker", "compose", "-f", str(compose), "up", "-d", "--build", "--wait", *SERVICES],
        capture_output=True,
        text=True,
        check=False,
    )
    if up.returncode != 0:
        raise PinecallError(COMPOSE_FAILED.format(output=(up.stderr or up.stdout).strip()[-2000:]))
    for service, port in PUBLISHED.items():
        answering(service, port)
    env = where / "env"
    values = read(env)
    settings = settings_of(values)
    applied = asyncio.run(apply_migrations(settings.database_url))
    if "PINECALL_WORKER_KEY" not in values:
        values["PINECALL_WORKER_KEY"] = asyncio.run(_minted(settings))
        with env.open("a", encoding="utf-8") as out:
            out.write(f"PINECALL_WORKER_KEY={values['PINECALL_WORKER_KEY']}\n")
    sys.stdout.write(UP.format(env=env, gateway=GATEWAY_URL))
    if applied.applied:
        sys.stdout.write(FIRST)
    sys.stdout.flush()
    if args.services_only:
        return 0
    return _both(values, ["gateway"], ["worker", "start"])


def local_down(_settings: Settings, args: argparse.Namespace) -> int:
    """Stop the services; the database's volume stays unless `--volumes`."""
    where = Path(args.dir)
    if which("docker") is None:
        raise PinecallError(NO_DOCKER)
    wiped = ["-v"] if args.volumes else []
    subprocess.run(
        ["docker", "compose", "-f", str(where / "compose.yaml"), "down", *wiped], check=True
    )
    sys.stdout.write((WIPED if args.volumes else DOWN).format(env=where / "env"))
    return 0


def local_env(_settings: Settings, args: argparse.Namespace) -> int:
    """Print the line a shell runs to use the local settings with any other verb."""
    sys.stdout.write(f"set -a; . {Path(args.dir) / 'env'}; set +a\n")
    return 0


def written(where: Path) -> Path:
    """Write the compose files the package ships and the settings once; the compose file's path."""
    shipped = files("pinecall.cli.local")
    for name in SHIPPED:
        target = where / name
        target.parent.mkdir(parents=True, exist_ok=True)
        content = shipped.joinpath(name).read_text(encoding="utf-8")
        if not target.is_file() or target.read_text(encoding="utf-8") != content:
            target.write_text(content, encoding="utf-8")
    (where / "recordings").mkdir(exist_ok=True)
    env = where / "env"
    if not env.is_file():
        env.touch(mode=0o600)
        env.write_text(
            SETTINGS.format(
                gateway=GATEWAY_URL,
                recordings=where / "recordings",
                vault=base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
                ops=secrets.token_hex(24),
                token=secrets.token_hex(32),
            ),
            encoding="utf-8",
        )
    return where / "compose.yaml"


def answering(service: str, port: int, within_s: float = PORTS_WITHIN_S) -> None:
    """Wait until the service accepts a connection on its loopback port, or say that it did not."""
    deadline = time.monotonic() + within_s
    while True:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1.0):
                return
        except OSError:
            if time.monotonic() > deadline:
                raise PinecallError(
                    NOT_ANSWERING.format(service=service, port=port, seconds=within_s)
                ) from None
            time.sleep(0.5)


def read(env: Path) -> dict[str, str]:
    """The settings file as `NAME=value` pairs."""
    pairs = (line.partition("=") for line in env.read_text(encoding="utf-8").splitlines())
    return {name: value for name, _, value in pairs if name and not name.startswith("#")}


def settings_of(values: dict[str, str]) -> Settings:
    """The runtime's settings from those pairs, over the process's own environment."""
    variables = {**os.environ, **values}
    return Settings.model_validate({**variables, "variables": variables})


def _on_the_local_settings(verb: Verb) -> Verb:
    def run(_settings: Settings, args: argparse.Namespace) -> int:
        return verb(settings_of(read(Path(args.dir) / "env")), args)

    return run


async def _minted(settings: Settings) -> str:
    issued = key_table.Issued(
        org=DEFAULT_ORG, env="sandbox", scopes=frozenset({THE_FLEET}), label="the sandbox fleet"
    )
    return await _operator.minted(settings, issued)


# Two processes of this same package, their lines told apart by a prefix, stopped together.
def _both(values: dict[str, str], *verbs: list[str]) -> int:
    children = [
        subprocess.Popen(
            [sys.executable, "-m", "pinecall.cli.main", *verb],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env={**os.environ, **values},
        )
        for verb in verbs
    ]
    readers = [
        threading.Thread(target=_relayed, args=(child.stdout, verb[0]), daemon=True)
        for child, verb in zip(children, verbs, strict=True)
    ]
    for reader in readers:
        reader.start()
    sys.stdout.write(RUNNING)
    sys.stdout.flush()

    def stop(_signal: int, _frame: object) -> None:
        for child in children:
            child.terminate()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    codes = [child.wait() for child in children]
    for reader in readers:
        reader.join(timeout=1)
    return 0 if all(code in (0, -signal.SIGTERM) for code in codes) else 1


def _relayed(out: IO[str] | None, prefix: str) -> None:
    if out is None:
        return
    for line in out:
        sys.stdout.write(f"{prefix:<8}| {line}")
        sys.stdout.flush()
