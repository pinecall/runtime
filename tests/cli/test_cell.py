"""Tests for `cell`: the box's verbs run its primary.sh, a machine's copy the package's files."""

import argparse
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from pinecall.cli import _cell
from pinecall.cli._box import BIN
from pinecall.cli._cell import ON_A_MACHINE, ON_THE_BOX, on_a_machine, on_the_box, steps_of
from pinecall.cli.main import verbs
from pinecall.domain.errors import DeclarationRefused
from pinecall.process.settings import Settings

SETTINGS = Settings.model_validate({})

INFRA = Path("/usr/lib/python3/site-packages/pinecall/infra")


def on_the_path(name: str, **_: object) -> str:
    """Every command found where a machine keeps it."""
    return f"/usr/local/bin/{name}"


def this_version(_: str) -> str:
    """The version pinecall says it is."""
    return "0.1.5"


class Ran:
    """Every command run, its argv kept, each answering with the code given."""

    def __init__(self, code: int = 0) -> None:
        """Nothing run yet."""
        self.code = code
        self.argvs: list[tuple[str, ...]] = []

    def run(self, argv: Sequence[str], **_: object) -> subprocess.CompletedProcess[str]:
        """Keep the argv and answer."""
        self.argvs.append(tuple(argv))
        return subprocess.CompletedProcess(argv, self.code)


def as_root(monkeypatch: pytest.MonkeyPatch, ran: Ran, primary: Path) -> None:
    """Root, uv on the PATH, the box's primary.sh at that path, the package's infra/ at INFRA."""
    monkeypatch.setattr(_cell.os, "geteuid", lambda: 0)
    monkeypatch.setattr(_cell.shutil, "which", on_the_path)
    monkeypatch.setattr(_cell.subprocess, "run", ran.run)
    monkeypatch.setattr(_cell, "PRIMARY", primary)
    monkeypatch.setattr(_cell, "infra_carried", lambda: INFRA)


def parsed(*argv: str) -> argparse.Namespace:
    """The CLI's own reading of a command line."""
    return verbs().parse_args(argv)


def test_each_box_verb_runs_its_primary_sh_verb_with_what_it_takes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    primary = tmp_path / "primary.sh"
    primary.write_text("#!/bin/sh\n")
    ran = Ran()
    as_root(monkeypatch, ran, primary)
    assert on_the_box(SETTINGS, parsed("cell", "allow-worker", "10.100.0.0/24")) == 0
    assert on_the_box(SETTINGS, parsed("cell", "worker-credentials", "production")) == 0
    assert on_the_box(SETTINGS, parsed("cell", "forget-replica")) == 0
    assert ran.argvs == [
        ("bash", str(primary), "allow-worker", "10.100.0.0/24"),
        ("bash", str(primary), "worker-credentials", "production"),
        ("bash", str(primary), "forget"),
    ]
    assert {"allow-replica", "allow-gateway", "allow-worker"} <= {v.verb for v in ON_THE_BOX}


def test_a_refusal_of_the_script_is_its_exit_and_no_box_is_said(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    primary = tmp_path / "primary.sh"
    primary.write_text("#!/bin/sh\n")
    as_root(monkeypatch, Ran(code=2), primary)
    assert on_the_box(SETTINGS, parsed("cell", "gateway-credentials")) == 2
    as_root(monkeypatch, Ran(), tmp_path / "nowhere.sh")
    with pytest.raises(DeclarationRefused, match="runs on the box"):
        on_the_box(SETTINGS, parsed("cell", "allow-gateway", "10.0.0.7"))


def test_a_worker_machine_copies_the_packages_files_then_joins_at_this_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ran = Ran()
    as_root(monkeypatch, ran, tmp_path / "primary.sh")
    monkeypatch.setattr(_cell, "version", this_version)
    joined = parsed("cell", "join-worker", "10.128.15.197", "production", "--calls", "16")
    assert on_a_machine(SETTINGS, joined) == 0
    copied, script = ran.argvs[-2:]
    assert copied == ("rsync", "-a", "--delete", f"{INFRA}/", "/opt/pinecall/infra/")
    assert script == (
        "bash",
        "/opt/pinecall/infra/cell/worker.sh",
        "join",
        "10.128.15.197",
        "pinecall==0.1.5",
        "production",
        "16",
    )


def test_an_option_left_unset_leaves_the_script_its_default_and_a_package_may_be_named(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ran = Ran()
    as_root(monkeypatch, ran, tmp_path / "primary.sh")
    wheel = str(tmp_path / "pinecall-0.1.5-py3-none-any.whl")
    imaged = parsed("cell", "image-worker", "10.0.0.5", "sandbox", "--package", wheel)
    assert on_a_machine(SETTINGS, imaged) == 0
    assert ran.argvs[-1][2:] == ("image", "10.0.0.5", wheel, "sandbox")
    assert on_a_machine(SETTINGS, parsed("cell", "join-replica", "10.0.0.5")) == 0
    assert ran.argvs[-1][1:] == ("/opt/pinecall/infra/cell/replica.sh", "join", "10.0.0.5")
    with pytest.raises(DeclarationRefused, match="pinecall==<version>"):
        on_a_machine(SETTINGS, parsed("cell", "release-worker", "--package", "latest"))


def test_uv_already_beside_the_runtime_is_not_copied_and_root_is_asked_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (joining,) = (named for named in ON_A_MACHINE if named.verb == "join-gateway")
    whats = [step.what for step in steps_of(joining, ("join", "b"), INFRA, BIN / "uv")]
    assert whats == ["the cell's files", joining.help]
    monkeypatch.setattr(_cell.os, "geteuid", lambda: 1000)
    with pytest.raises(DeclarationRefused, match="as root"):
        on_a_machine(SETTINGS, parsed("cell", "release-gateway"))
