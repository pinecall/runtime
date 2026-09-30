"""`box up` and `box upgrade`: this machine made a Pinecall box from the package, no checkout."""

import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from importlib import resources
from importlib.metadata import version
from pathlib import Path

from pinecall.domain.errors import DeclarationRefused
from pinecall.process.settings import Settings

# What cloud-init installs on a box `make box` reaches; `box up` installs it on this one.
SYSTEM_PACKAGES = ("podman", "caddy", "nftables", "curl", "rsync", "openssl", "age")

OPT = Path("/opt/pinecall")

BOX_ENV = Path("/etc/pinecall/box.env")

BACKUP_KEY = Path("/etc/pinecall/backup.age.pub")

NOT_ROOT = "box {verb} makes this machine a box: run it as root (sudo)"

NOT_APT = "box {verb} installs with apt: it runs on Ubuntu 24.04 (or Debian 13), not here"

NO_UV = (
    "no uv on the PATH: "
    "`curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_INSTALL_DIR=/usr/local/bin sh`"
)

NO_INFRA = (
    "this install of pinecall carries no infra/: install it from PyPI (`uvx --from pinecall …`)"
)

NO_DOMAINS = "--domains <production>[,<sandbox>]: the box's names, already pointed at this machine"

NOT_AN_AGE_KEY = "--backup-key {key}: an age public key, age1…"

NO_BOX = "no box here yet ({env} is missing): `box up --domains …` makes one"

NO_BACKUPS = "off — `box up --backup-key age1…` turns them on (the private key stays with you)"

NEXT = """
the box is up at https://{first}
  sudo pinecall-runtime init --org <slug> --email <you> --person "<name>"   the first org and person
  sudo pinecall-runtime doctor                                             what this box lacks
  sudo uvx --from pinecall@latest pinecall-runtime box upgrade             the newest pinecall
backups: {backups}
"""


@dataclass(frozen=True)
class Step:
    """One thing done to the machine: what it is, and the command that does it."""

    what: str
    argv: tuple[str, ...]
    env: dict[str, str] = field(default_factory=dict[str, str])


INFRA = OPT / "infra"

BIN = OPT / "bin"


def box_group(group: argparse.ArgumentParser) -> None:
    """`box up` and `box upgrade`."""
    verbs = group.add_subparsers(required=True)
    up = verbs.add_parser("up", help="make this machine a box, or bring it to this version")
    up.add_argument("--domains", default=None, help=NO_DOMAINS)
    up.add_argument("--backup-key", default=None, help="the age public key backups encrypt to")
    up.add_argument("--package", default=None, help="install this instead of pinecall==<this>")
    up.set_defaults(run=box_up)
    upgrade = verbs.add_parser("upgrade", help="this box brought to this version, its names kept")
    upgrade.add_argument("--package", default=None, help="install this instead of pinecall==<this>")
    upgrade.set_defaults(run=box_upgrade)


def box_up(_settings: Settings, args: argparse.Namespace) -> int:
    """Make this machine a box at the names given, running this version of pinecall."""
    _refuse_elsewhere("up")
    domains = args.domains or _domains_kept()
    if not domains:
        raise DeclarationRefused(NO_DOMAINS)
    if args.backup_key is not None and not args.backup_key.startswith("age1"):
        raise DeclarationRefused(NOT_AN_AGE_KEY.format(key=args.backup_key))
    return _made(domains, args.backup_key, args.package or f"pinecall=={version('pinecall')}")


def box_upgrade(_settings: Settings, args: argparse.Namespace) -> int:
    """This box, at the names it has, brought to this version of pinecall."""
    _refuse_elsewhere("upgrade")
    domains = _domains_kept()
    if not domains:
        raise DeclarationRefused(NO_BOX.format(env=BOX_ENV))
    return _made(domains, None, args.package or f"pinecall=={version('pinecall')}")


def steps_of(domains: str, package: str, infra: Path, uv: Path) -> list[Step]:
    """Every step that makes a machine a box, in order; the same list brings one up to date."""
    names = ",".join(name.strip() for name in domains.split(",") if name.strip())
    return [
        Step("the system's packages", ("apt-get", "update", "-q")),
        Step(
            "podman, caddy, nftables, age",
            ("apt-get", "install", "-y", "-q", *SYSTEM_PACKAGES),
            {"DEBIAN_FRONTEND": "noninteractive"},
        ),
        Step("the firewall on at boot", ("systemctl", "enable", "--now", "nftables")),
        Step("uv beside the runtime", ("install", "-D", "-m", "755", str(uv), str(BIN / "uv"))),
        Step("the box's files", ("rsync", "-a", "--delete", f"{infra}/", f"{INFRA}/")),
        Step("the box installed", ("bash", str(INFRA / "box" / "install.sh"), names)),
        Step(
            f"{package} released",
            ("bash", str(INFRA / "box" / "release.sh")),
            {"PACKAGE": package},
        ),
        Step("the venv the deploy account's", ("chown", "-R", "deploy:deploy", str(OPT / "venv"))),
    ]


def domains_in(box_env: str) -> str:
    """The box's names as `box up` takes them, read from /etc/pinecall/box.env."""
    for line in box_env.splitlines():
        if line.startswith("PINECALL_DOMAINS="):
            value = line.partition("=")[2]
            return ",".join(name.strip() for name in value.split(",") if name.strip())
    return ""


def _made(domains: str, backup_key: str | None, package: str) -> int:
    uv = shutil.which("uv")
    if uv is None:
        raise DeclarationRefused(NO_UV)
    infra = _infra_carried()
    if backup_key is not None:
        BACKUP_KEY.parent.mkdir(parents=True, exist_ok=True)
        BACKUP_KEY.write_text(f"{backup_key}\n")
    for step in steps_of(domains, package, infra, Path(uv)):
        sys.stdout.write(f"→ {step.what}\n")
        sys.stdout.flush()
        subprocess.run(step.argv, check=True, env={**os.environ, **step.env})
    backups = f"nightly at 03:00, to {BACKUP_KEY}" if BACKUP_KEY.exists() else NO_BACKUPS
    first = domains.split(",", 1)[0].strip()
    sys.stdout.write(NEXT.format(first=first, backups=backups))
    return 0


def _infra_carried() -> Path:
    carried = resources.files("pinecall") / "infra"
    path = Path(str(carried))
    if not (path / "box" / "install.sh").is_file():
        raise DeclarationRefused(NO_INFRA)
    return path


def _domains_kept() -> str:
    return domains_in(BOX_ENV.read_text()) if BOX_ENV.exists() else ""


def _refuse_elsewhere(verb: str) -> None:
    if os.geteuid() != 0:
        raise DeclarationRefused(NOT_ROOT.format(verb=verb))
    if shutil.which("apt-get") is None:
        raise DeclarationRefused(NOT_APT.format(verb=verb))
