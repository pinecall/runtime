"""Tests for `box up` and `box upgrade`: the steps in order, the names kept, the refusals first."""

import argparse
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from pinecall.cli import _box
from pinecall.cli._box import (
    BIN,
    SYSTEM_PATH,
    box_failover,
    box_up,
    box_upgrade,
    domains_in,
    steps_of,
)
from pinecall.domain.errors import DeclarationRefused
from pinecall.process.settings import Settings

INFRA = Path("/usr/lib/python3/site-packages/pinecall/infra")
UV = Path("/usr/local/bin/uv")

SETTINGS = Settings.model_validate({})


def on_the_path(name: str) -> str:
    """Every command found where a machine keeps it."""
    return f"/usr/bin/{name}"


def test_a_box_is_made_in_order_from_the_packages_own_files() -> None:
    steps = steps_of(" box.x.com , sandbox.x.com ", "pinecall==0.1.2", INFRA, UV)
    whats = [step.what for step in steps]
    assert whats[0] == "the system's packages"
    assert whats.index("the box's files") < whats.index("the box installed")
    assert whats.index("the box installed") < whats.index("pinecall==0.1.2 released")
    installed = next(step for step in steps if step.what == "the box installed")
    assert installed.argv == (
        "bash",
        "/opt/pinecall/infra/box/install.sh",
        "box.x.com,sandbox.x.com",
    )
    copied = next(step for step in steps if step.what == "the box's files")
    assert copied.argv[-2:] == (f"{INFRA}/", "/opt/pinecall/infra/")
    released = next(step for step in steps if step.what.endswith("released"))
    assert released.env == {"PACKAGE": "pinecall==0.1.2"}


def test_uv_already_beside_the_runtime_is_not_copied_onto_itself() -> None:
    whats = [step.what for step in steps_of("box.x.com", "pinecall==0.1.2", INFRA, BIN / "uv")]
    assert "uv beside the runtime" not in whats
    assert "uv beside the runtime" in [step.what for step in steps_of("b.x", "p", INFRA, UV)]


def test_every_step_runs_on_the_systems_whole_path() -> None:
    assert {"/usr/sbin", "/sbin", str(BIN)} <= set(SYSTEM_PATH.split(":"))


def test_the_names_a_box_has_are_read_back_as_box_up_takes_them() -> None:
    kept = "PINECALL_DOMAIN=box.x.com\nPINECALL_DOMAINS=box.x.com, sandbox.x.com\n"
    assert domains_in(kept) == "box.x.com,sandbox.x.com"
    assert domains_in("PINECALL_DOMAIN=box.x.com\n") == ""


def test_a_machine_that_is_not_root_is_refused_before_anything_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_box.os, "geteuid", lambda: 1000)
    wanted = argparse.Namespace(domains="box.x.com", backup_key=None, package=None)
    with pytest.raises(DeclarationRefused, match="as root"):
        box_up(SETTINGS, wanted)


def test_a_backup_key_that_is_no_age_key_and_an_upgrade_with_no_box_are_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(_box.os, "geteuid", lambda: 0)
    monkeypatch.setattr(_box.shutil, "which", on_the_path)
    monkeypatch.setattr(_box, "BOX_ENV", tmp_path / "box.env")
    wanted = argparse.Namespace(domains="box.x.com", backup_key="ssh-ed25519 AAAA", package=None)
    with pytest.raises(DeclarationRefused, match="an age public key"):
        box_up(SETTINGS, wanted)
    with pytest.raises(DeclarationRefused, match="no box here yet"):
        box_upgrade(SETTINGS, argparse.Namespace(package=None))
    with pytest.raises(DeclarationRefused, match="--domains"):
        box_up(SETTINGS, argparse.Namespace(domains=None, backup_key=None, package=None))


class Standby:
    """The container's Postgres as `podman exec … psql` answers it: in recovery until promoted."""

    def __init__(self, *, in_recovery: bool = True, promotes: bool = True) -> None:
        """A standby, or a primary when printed it is not in recovery."""
        self.in_recovery = in_recovery
        self.promotes = promotes
        self.queries: list[str] = []

    def run(self, argv: Sequence[str], **_: object) -> subprocess.CompletedProcess[str]:
        """Answer the query the argv ends with."""
        assert tuple(argv[:3]) == ("podman", "exec", "pinecall-postgres")
        query = argv[-1]
        self.queries.append(query)
        answer = ""
        if query == _box.IN_RECOVERY:
            answer = "t" if self.in_recovery else "f"
        elif query == _box.LAST_REPLAYED:
            answer = "2026-09-30 14:05:12.3+00"
        elif query == _box.PROMOTE:
            self.in_recovery = not self.promotes
            answer = "t" if self.promotes else "f"
        return subprocess.CompletedProcess(argv, 0, stdout=f"{answer}\n", stderr="")


def on_this_machine(monkeypatch: pytest.MonkeyPatch, postgres: Standby) -> None:
    """Root on an apt machine whose container answers as the standby does."""
    monkeypatch.setattr(_box.os, "geteuid", lambda: 0)
    monkeypatch.setattr(_box.shutil, "which", on_the_path)
    monkeypatch.setattr(_box.subprocess, "run", postgres.run)


def test_a_standby_is_promoted_and_the_operator_told_what_to_repoint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    postgres = Standby()
    on_this_machine(monkeypatch, postgres)
    assert box_failover(SETTINGS, argparse.Namespace()) == 0
    assert postgres.queries == [
        _box.IN_RECOVERY,
        _box.LAST_REPLAYED,
        _box.PROMOTE,
        _box.IN_RECOVERY,
    ]
    printed = capsys.readouterr().out
    assert "the last write it replayed was at 2026-09-30 14:05:12.3+00" in printed
    assert "Nothing else was changed, repointed or deleted" in printed
    assert "box up --domains <production>,<sandbox>" in printed


def test_a_primary_is_never_promoted_and_a_promotion_that_did_not_take_is_said(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary = Standby(in_recovery=False)
    on_this_machine(monkeypatch, primary)
    with pytest.raises(DeclarationRefused, match="is none"):
        box_failover(SETTINGS, argparse.Namespace())
    assert _box.PROMOTE not in primary.queries
    stuck = Standby(promotes=False)
    on_this_machine(monkeypatch, stuck)
    with pytest.raises(DeclarationRefused, match="did not leave recovery"):
        box_failover(SETTINGS, argparse.Namespace())


def test_a_machine_with_no_postgres_container_is_refused_with_podmans_words(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(argv: Sequence[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 125, stdout="", stderr="no such container")

    on_this_machine(monkeypatch, Standby())
    monkeypatch.setattr(_box.subprocess, "run", missing)
    with pytest.raises(DeclarationRefused, match="no such container"):
        box_failover(SETTINGS, argparse.Namespace())
