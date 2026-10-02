"""`cell`: the machines beside the box, let in from the box and joined from the package, as root."""

import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path

import httpx

from pinecall.cli import _enroll
from pinecall.cli._box import INFRA, NO_UV, SYSTEM_PATH, Step, infra_carried, uv_beside
from pinecall.domain.errors import DeclarationRefused
from pinecall.process.settings import Settings

# The box's verbs run the copy `box up` installed: the scripts of the version the box runs.
PRIMARY = INFRA / "cell" / "primary.sh"

NOT_ROOT = "cell {verb} changes this machine's firewall, containers and credentials: run it as root"

NOT_A_BOX = "cell {verb} runs on the box, and {script} is not here: `box up` puts it there"

NOT_A_PACKAGE = "--package is a wheel's path or pinecall==<version>, not {package}"


@dataclass(frozen=True)
class BoxVerb:
    """A verb of the box's side: the primary.sh verb it is, and what it takes."""

    verb: str
    script_verb: str
    takes: tuple[str, ...]
    help: str


@dataclass(frozen=True)
class MachineVerb:
    """A verb run on the machine joined: its script, the script's words, and what it takes."""

    verb: str
    script: str
    words: tuple[str, ...]
    takes: tuple[str, ...]
    help: str
    counted: str | None = None


# Each the box's side of a machine of the cell (infra/cell/primary.sh, its header says the rest).
ON_THE_BOX = (
    BoxVerb("allow-replica", "allow", ("address",), "a streaming replica of Postgres there"),
    BoxVerb("forget-replica", "forget", (), "the replica's slot, fence and publishing gone"),
    BoxVerb("allow-gateway", "allow-gateway", ("address",), "a gateway machine at that address"),
    BoxVerb("forget-gateway", "forget-gateway", ("address",), "that gateway machine let go"),
    BoxVerb("gateway-credentials", "gateway-credentials", (), "a gateway machine's secrets, a tar"),
    BoxVerb("allow-worker", "allow-worker", ("address",), "a worker machine, or the fleet's range"),
    BoxVerb("forget-worker", "forget-worker", ("address",), "that worker machine or range let go"),
    BoxVerb("worker-settings", "worker-settings", ("world",), "a worker's settings, no secret"),
    BoxVerb("worker-credentials", "worker-credentials", ("world",), "a worker's secrets, a tar"),
)


# The words are the script's argv, each `{name}` read from the verb's own; an option left unset
# (`{calls}`, `{processes}`) leaves the script its default.
ON_A_MACHINE = (
    MachineVerb(
        "join-worker",
        "worker.sh",
        ("join", "{box}", "{package}", "{world}", "{calls}"),
        ("box", "world"),
        "this machine a worker of the world's fleet, its credentials on stdin",
        "calls",
    ),
    MachineVerb(
        "image-worker",
        "worker.sh",
        ("image", "{box}", "{package}", "{world}", "{calls}"),
        ("box", "world"),
        "this machine made the fleet's image, its settings on stdin and no credential",
        "calls",
    ),
    MachineVerb(
        "release-worker", "worker.sh", ("release", "{package}"), (), "this worker on a new version"
    ),
    MachineVerb(
        "join-gateway",
        "gateway.sh",
        ("join", "{box}", "{package}", "{processes}"),
        ("box",),
        "this machine a gateway machine, the box's credentials on stdin",
        "processes",
    ),
    MachineVerb(
        "release-gateway", "gateway.sh", ("release", "{package}"), (), "these gateways, new version"
    ),
    MachineVerb(
        "join-replica",
        "replica.sh",
        ("join", "{box}"),
        ("box",),
        "this machine a replica of the box's Postgres, the replication password on stdin",
    ),
)


def cell_group(group: argparse.ArgumentParser) -> None:
    """The box's side of each machine of the cell, and each machine joined."""
    verbs = group.add_subparsers(required=True)
    for named in ON_THE_BOX:
        verb = verbs.add_parser(named.verb, help=named.help)
        for taken in named.takes:
            verb.add_argument(taken)
        verb.set_defaults(run=on_the_box, cell=named)
    for named in ON_A_MACHINE:
        verb = verbs.add_parser(named.verb, help=named.help)
        for taken in named.takes:
            verb.add_argument(taken)
        if named.counted is not None:
            verb.add_argument(f"--{named.counted}", type=int, default=None)
        verb.add_argument("--package", default=None, help="instead of pinecall==<this version>")
        verb.set_defaults(run=on_a_machine, cell=named)
    enrolling = "a fleet machine's first boot: its credentials by a join token or its cloud's store"
    verbs.add_parser("enroll", help=enrolling).set_defaults(run=enroll_here)
    publishing = "the box's credentials put in Secret Manager, for its fleet's machines to read"
    verbs.add_parser("publish-secrets", help=publishing).set_defaults(run=publish_here)


# The script writes to stdout what a pipe carries (the credentials' tar), so nothing else does.
def on_the_box(_settings: Settings, args: argparse.Namespace) -> int:
    """The box's side of a machine of the cell, as the box's own primary.sh does it."""
    named: BoxVerb = args.cell
    _refuse_unless_root(named.verb)
    if not PRIMARY.is_file():
        raise DeclarationRefused(NOT_A_BOX.format(verb=named.verb, script=PRIMARY))
    argv = ("bash", str(PRIMARY), named.script_verb, *(getattr(args, t) for t in named.takes))
    return subprocess.run(argv, check=False, env={**os.environ, "PATH": SYSTEM_PATH}).returncode


def on_a_machine(_settings: Settings, args: argparse.Namespace) -> int:
    """This machine joined to the cell, or released: the package's infra/, then its script."""
    named: MachineVerb = args.cell
    _refuse_unless_root(named.verb)
    uv = shutil.which("uv", path=SYSTEM_PATH)
    if uv is None:
        raise DeclarationRefused(NO_UV)
    package = args.package or f"pinecall=={version('pinecall')}"
    if not (package.endswith(".whl") or package.startswith("pinecall==")):
        raise DeclarationRefused(NOT_A_PACKAGE.format(package=package))
    for step in steps_of(named, script_words(named, args, package), infra_carried(), Path(uv)):
        sys.stderr.write(f"→ {step.what}\n")
        sys.stderr.flush()
        subprocess.run(step.argv, check=True, env={**os.environ, "PATH": SYSTEM_PATH})
    return 0


# pinecall-join.service runs it at every boot: a machine enrolled already, or joined by hand, is
# told so and nothing changes.
def enroll_here(_settings: Settings, _args: argparse.Namespace) -> int:
    """This fleet machine's credentials sealed here, once."""
    _refuse_unless_root("enroll")
    with httpx.Client(timeout=_enroll.TIMEOUT_S) as http:
        sys.stdout.write(f"{_enroll.enroll(http)}\n")
    return 0


def publish_here(_settings: Settings, _args: argparse.Namespace) -> int:
    """The box's credentials in Secret Manager, a version added only where one differs."""
    _refuse_unless_root("publish-secrets")
    with httpx.Client(timeout=_enroll.TIMEOUT_S) as http:
        written = _enroll.publish(http)
    sys.stdout.write(f"published: {', '.join(written) or 'nothing, every secret was current'}\n")
    return 0


def script_words(named: MachineVerb, args: argparse.Namespace, package: str) -> tuple[str, ...]:
    """The script's argv after its path: each `{name}` filled, an option left unset dropped."""
    given = {taken: getattr(args, taken) for taken in named.takes}
    given["package"] = package
    if named.counted is not None:
        given[named.counted] = getattr(args, named.counted)
    words: list[str] = []
    for word in named.words:
        if not word.startswith("{"):
            words.append(word)
            continue
        value = given[word.strip("{}")]
        if value is not None:
            words.append(str(value))
    return tuple(words)


def steps_of(named: MachineVerb, words: tuple[str, ...], infra: Path, uv: Path) -> list[Step]:
    """What joining or releasing does to this machine, in order: uv, the files, the script."""
    return [
        *uv_beside(uv.resolve()),
        Step("the cell's files", ("rsync", "-a", "--delete", f"{infra}/", f"{INFRA}/")),
        Step(named.help, ("bash", str(INFRA / "cell" / named.script), *words)),
    ]


def _refuse_unless_root(verb: str) -> None:
    if os.geteuid() != 0:
        raise DeclarationRefused(NOT_ROOT.format(verb=verb))
