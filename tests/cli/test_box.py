"""Tests for `box up` and `box upgrade`: the steps in order, the names kept, the refusals first."""

import argparse
from pathlib import Path

import pytest

from pinecall.cli import _box
from pinecall.cli._box import box_up, box_upgrade, domains_in, steps_of
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
