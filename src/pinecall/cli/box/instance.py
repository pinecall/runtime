"""`pinecall-runtime box instance <name>`: one instance of the runtime on a box, as one env file."""

import argparse
import re
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO
from urllib.parse import urlsplit

from pinecall._exceptions import PinecallError
from pinecall._settings import NOBODY_TO_ASK, variable_of
from pinecall.types import PRODUCTION, SANDBOX, Env
from pinecall.types.dispatch import A_FLEET_NAME, DEFAULT_FLEET

# An instance is two things on the box and nothing else: its env file, which every unit of it reads
# after box.env (and wins over it), and its credstore, the secrets it holds alone. Neither is inside
# /etc/credstore.encrypted: `ImportCredential=` and the doctor read EVERY entry there, and one
# instance's ops key must never be an entry another instance can import.
INSTANCES = Path("/etc/pinecall/instances")
RECORDINGS = Path("/var/lib/pinecall/recordings")

# The instance a box has always had, and the one `make install` writes from box.env when it is
# missing. Its database is the container's own and its fleet the default one.
THE_FIRST = "production"

# A name becomes a unit instance (`pinecall-gateway@<name>`), two paths, a Caddy site file and, for
# every instance but the first, a Postgres database and role (`pinecall_<name>`): the slug alphabet,
# short enough that `pinecall_` and the name fit Postgres' 63 bytes with room to spare.
AN_INSTANCE_NAME = r"^[a-z0-9][a-z0-9-]{0,30}$"

# Ports come in hundreds so an instance's two never meet another's: the gateway on the hundred, its
# worker's health server two above it (8082 beside 8080, as it always was). 8081 is TEI's and 8090
# the notifier's, neither on a hundred's gateway or worker port.
FIRST_PORT = 8080
A_HUNDRED = 100
WORKER_BESIDE = 2

NOT_A_NAME = "{name} is not an instance name: lowercase letters, digits and dashes, 32 at most"
NOT_A_FLEET = "{fleet} is not a fleet name: lowercase letters, digits and dashes"
ALREADY = "{path} is already there: --force writes it again"
PORT_TAKEN = "port {port} is {other}'s: an instance's gateway and worker take {port} and {port} + 2"
WROTE = "wrote {path}"
NEXT = (
    "next: `pinecall-runtime box secrets --instance {name}`, {name} in PINECALL_INSTANCES "
    "(/etc/pinecall/box.env), then `make deploy`"
)


class InstanceRefused(PinecallError):
    """An instance this verb will not write: its name, its fleet, its port or its identity."""


def env_file(name: str, instances: Path = INSTANCES) -> Path:
    """Where one instance's environment is: `/etc/pinecall/instances/<name>.env`."""
    return instances / f"{name}.env"


def credstore_of(name: str, instances: Path = INSTANCES) -> Path:
    """Where one instance's own secrets are: `/etc/pinecall/instances/<name>.credstore/`."""
    return instances / f"{name}.credstore"


def a_name(name: str) -> str:
    """The name, or the refusal: it is a unit, a path and a database before it is anything else."""
    if not re.match(AN_INSTANCE_NAME, name):
        raise InstanceRefused(NOT_A_NAME.format(name=name))
    return name


def database_of(name: str) -> str:
    """The database and role of an instance that is not the first: a dash is not an identifier."""
    return f"pinecall_{name.replace('-', '_')}"


@dataclass(frozen=True)
class Instance:
    """What one instance's env file says, every variable written — empty for one it leaves unset."""

    name: str
    world: Env
    domain: str
    port: int
    fleet: str
    recordings: Path
    identity: str | None = None
    elsewhere: str | None = None
    sandbox: str | None = None
    max_jobs: int | None = None
    idle_processes: int | None = None

    # EVERY variable, the unset ones as `NAME=` (which the settings read as absent): box.env is read
    # first, and a line it still carries from a box of one instance — PINECALL_ELSEWHERE_URL, say —
    # must not become a second instance's by nobody writing its own.
    def lines(self) -> list[str]:
        """The file, one `NAME=value` per variable, under a line saying what wrote it."""
        values: dict[str, object] = {
            "world": self.world,
            "fleet": self.fleet,
            "domain": self.domain,
            "gateway_url": f"http://127.0.0.1:{self.port}",
            "worker_http_port": self.port + WORKER_BESIDE,
            "recordings_root": self.recordings,
            "identity_url": self.identity,
            "elsewhere_url": self.elsewhere,
            "sandbox_url": self.sandbox,
            "max_jobs": self.max_jobs,
            "idle_processes": self.idle_processes,
        }
        said = [
            f"{variable_of(field)}={'' if value is None else value}"
            for field, value in values.items()
        ]
        return [f"# The instance {self.name}, written by `pinecall-runtime box instance`.", *said]


def configure(parser: argparse.ArgumentParser) -> None:
    """`box instance <name> --world … --domain …`, and the knobs a second instance may want."""
    parser.add_argument("name", help="production, sandbox, staging…: its units are …@<name>")
    parser.add_argument("--world", required=True, choices=(PRODUCTION, SANDBOX))
    parser.add_argument("--domain", required=True, help="the public name Caddy answers it at")
    parser.add_argument("--port", type=int, help="its gateway's; default the next free hundred")
    parser.add_argument(
        "--fleet", help=f"default {DEFAULT_FLEET} for production, else pinecall-<name>"
    )
    parser.add_argument("--identity", help="production's URL: required for a sandbox")
    parser.add_argument("--elsewhere", help="the other world's URL, named in every refusal")
    parser.add_argument("--sandbox", help="production's: its sandbox's URL, asked whose a ring is")
    parser.add_argument("--max-jobs", type=int, help="calls its worker holds, measured")
    parser.add_argument("--idle-processes", type=int, help="job processes its worker keeps warm")
    parser.add_argument("--force", action="store_true", help="write over the file that is there")
    parser.add_argument("--into", type=Path, default=INSTANCES, help=f"default {INSTANCES}")
    parser.set_defaults(run=run_instance)


def run_instance(arguments: argparse.Namespace) -> int:
    """The flags, as an Instance, into its file."""
    instance = declared(
        arguments.name,
        arguments.world,
        arguments.domain,
        arguments.into,
        port=arguments.port,
        fleet=arguments.fleet,
        identity=arguments.identity,
        elsewhere=arguments.elsewhere,
        sandbox=arguments.sandbox,
        max_jobs=arguments.max_jobs,
        idle_processes=arguments.idle_processes,
    )
    return write_instance(instance, arguments.into, force=arguments.force)


def declared(
    name: str,
    world: Env,
    domain: str,
    instances: Path,
    *,
    port: int | None = None,
    fleet: str | None = None,
    identity: str | None = None,
    elsewhere: str | None = None,
    sandbox: str | None = None,
    max_jobs: int | None = None,
    idle_processes: int | None = None,
) -> Instance:
    """The instance the flags describe, every default filled — or the refusal, in one sentence."""
    a_name(name)
    if world == SANDBOX and not identity:
        raise InstanceRefused(NOBODY_TO_ASK)
    fleet = fleet or (DEFAULT_FLEET if name == THE_FIRST else f"{DEFAULT_FLEET}-{name}")
    if not re.match(A_FLEET_NAME, fleet):
        raise InstanceRefused(NOT_A_FLEET.format(fleet=fleet))
    taken = ports_taken(instances, but=name)
    port = port or a_free_port(name, taken)
    for mine in (port, port + WORKER_BESIDE):
        if mine in taken:
            raise InstanceRefused(PORT_TAKEN.format(port=port, other=taken[mine]))
    return Instance(
        name=name,
        world=world,
        domain=domain,
        port=port,
        fleet=fleet,
        recordings=RECORDINGS / name,
        identity=identity,
        elsewhere=elsewhere,
        sandbox=sandbox,
        max_jobs=max_jobs,
        idle_processes=idle_processes,
    )


def ports_taken(instances: Path, *, but: str) -> dict[int, str]:
    """Every port another instance's file holds — its gateway's and its worker's — by owner."""
    taken: dict[int, str] = {}
    for other in sorted(instances.glob("*.env")):
        if other.stem == but:
            continue
        port = _the_port_in(other)
        if port is not None:
            taken |= {port: other.stem, port + WORKER_BESIDE: other.stem}
    return taken


def a_free_port(name: str, taken: dict[int, str]) -> int:
    """8080 for the first instance; the next hundred whose two ports are free for any other."""
    if name == THE_FIRST:
        return FIRST_PORT
    port = FIRST_PORT + A_HUNDRED
    while port in taken or port + WORKER_BESIDE in taken:
        port += A_HUNDRED
    return port


def write_instance(
    instance: Instance,
    instances: Path,
    *,
    force: bool = False,
    out: TextIO = sys.stdout,
    keep_recordings: Callable[[Path], None] | None = None,
) -> int:
    """The file, written whole, and its recordings' directory; never over a file unasked."""
    path = env_file(instance.name, instances)
    if path.exists() and not force:
        raise InstanceRefused(ALREADY.format(path=path))
    instances.mkdir(mode=0o755, parents=True, exist_ok=True)
    path.write_text("\n".join(instance.lines()) + "\n")
    path.chmod(0o644)
    (keep_recordings or recordings_directory)(instance.recordings)
    print(WROTE.format(path=path), file=out)
    print(NEXT.format(name=instance.name), file=out)
    return 0


# The same owner and mode the root has (infra/box/tmpfiles.d): the worker writes as `pinecall`, the
# recorder as a member of `pinecall-media`, and setgid keeps what either writes readable by both.
def recordings_directory(path: Path) -> None:
    """`/var/lib/pinecall/recordings/<name>`, 2770 pinecall:pinecall-media, as root makes it."""
    path.mkdir(parents=True, exist_ok=True)
    shutil.chown(path, user="pinecall", group="pinecall-media")
    path.chmod(0o2770)


def _the_port_in(path: Path) -> int | None:
    """The port of the PINECALL_GATEWAY_URL line of an env file, if it says one."""
    url = said_in(path, "gateway_url")
    if url is None:
        return None
    try:
        return urlsplit(url).port
    except ValueError:
        return None


# The file is ours and flat — `NAME=value`, one per line, no quoting — so it is read as it is
# written, by the one variable name the settings give the field.
def said_in(path: Path, field: str) -> str | None:
    """What one instance's env file says of one settings field, or None when it is unset."""
    prefix = f"{variable_of(field)}="
    for line in path.read_text().splitlines():
        if line.startswith(prefix):
            return line.removeprefix(prefix).strip() or None
    return None
