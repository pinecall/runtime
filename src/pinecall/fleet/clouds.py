"""A cloud as the loop sees it: three verbs of one script — create, delete, list — and no SDK."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# The scripts this repository ships, one per provider, under infra/fleet/: ~40 lines of the
# vendor's own CLI each, and the only cloud-specific code there is. `--cloud gcp` names one;
# `--cloud ./mine` names yours.
SCRIPTS = Path(__file__).resolve().parents[3] / "infra" / "fleet"

# A cloud call is a network round trip and sometimes a boot; two minutes is generous and finite.
A_VERB_MAY_TAKE_S = 120.0


@dataclass(frozen=True)
class Machine:
    """One machine the cloud lists as the fleet's: its name, and when it was made."""

    name: str
    created_at: float


class CloudRefused(Exception):
    """The script exited non-zero; the message is its stderr, for the terminal."""


class Script:
    """The cloud behind one executable: `<script> create <name>`, `delete <name>`, `list`."""

    def __init__(self, path: Path) -> None:
        if not path.is_file():
            raise CloudRefused(f"no cloud script at {path}")
        self._path = path

    def machines(self) -> tuple[Machine, ...]:
        """`list`: one machine per line, `name<TAB>created-at` with the time in ISO 8601."""
        said = self._run("list")
        return tuple(_a_machine(line) for line in said.splitlines() if line.strip())

    def create(self, name: str) -> None:
        """`create <name>`: a machine that boots from the image and dials the hub by itself."""
        self._run("create", name)

    def delete(self, name: str) -> None:
        """`delete <name>`: the machine is gone. The loop only asks once it holds no call."""
        self._run("delete", name)

    def _run(self, *verb: str) -> str:
        """One verb, its stdout back, and its stderr as the refusal when it fails."""
        try:
            done = subprocess.run(  # noqa: S603 — the path is the operator's, not a request's
                [str(self._path), *verb],
                capture_output=True,
                text=True,
                check=False,
                timeout=A_VERB_MAY_TAKE_S,
            )
        except subprocess.TimeoutExpired as slow:
            raise CloudRefused(
                f"{self._path.name} {' '.join(verb)}: no answer in {A_VERB_MAY_TAKE_S:.0f}s"
            ) from slow
        if done.returncode != 0:
            raise CloudRefused(
                f"{self._path.name} {' '.join(verb)}: {done.stderr.strip() or done.returncode}"
            )
        return done.stdout


def cloud_named(name: str) -> Script:
    """`gcp`, `aws`, `hetzner` out of infra/fleet/, or any path to a script of your own."""
    path = Path(name)
    if path.exists():
        return Script(path.resolve())
    return Script(SCRIPTS / name)


def _a_machine(line: str) -> Machine:
    """One line of `list`, as the scripts print it."""
    name, _, created = line.partition("\t")
    try:
        when = datetime.fromisoformat(created.strip())
    except ValueError as unreadable:
        raise CloudRefused(f"list: {created!r} is not an ISO 8601 time") from unreadable
    return Machine(name=name.strip(), created_at=when.timestamp())
