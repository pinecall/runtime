"""A cloud is a script with three verbs; the adapter reads its list and passes its refusals on."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from pinecall.fleet import Machine, Script, cloud_named
from pinecall.fleet.clouds import SCRIPTS, CloudRefused

pytestmark = pytest.mark.unit

A_SCRIPT = """#!/bin/sh
case "$1" in
  list) printf 'pinecall-worker-1\\t2026-09-11T12:00:00+00:00\\n'
        printf 'pinecall-worker-2\\t2026-09-11T12:30:00.000-07:00\\n' ;;
  create) echo "made $2" >> "$(dirname "$0")/log" ;;
  delete) echo "no such machine $2" >&2; exit 1 ;;
esac
"""


@pytest.fixture
def script(tmp_path: Path) -> Path:
    path = tmp_path / "mine"
    path.write_text(A_SCRIPT)
    path.chmod(0o755)
    return path


def test_the_list_is_one_machine_per_line_with_its_creation_time_in_iso_8601(script: Path) -> None:
    machines = Script(script).machines()
    assert [one.name for one in machines] == ["pinecall-worker-1", "pinecall-worker-2"]
    noon = datetime(2026, 9, 11, 12, tzinfo=UTC).timestamp()
    assert machines[0] == Machine("pinecall-worker-1", noon)
    # An offset is honoured: 12:30 at -07:00 is 19:30 UTC.
    assert machines[1].created_at == noon + 7 * 3600 + 1800


def test_create_runs_the_script_with_the_name(script: Path) -> None:
    Script(script).create("pinecall-worker-3")
    assert (script.parent / "log").read_text() == "made pinecall-worker-3\n"


def test_a_non_zero_exit_is_a_refusal_carrying_the_scripts_own_stderr(script: Path) -> None:
    with pytest.raises(CloudRefused, match="no such machine pinecall-worker-9"):
        Script(script).delete("pinecall-worker-9")


def test_a_name_is_one_of_the_shipped_scripts_and_a_path_is_yours(script: Path) -> None:
    assert cloud_named(str(script))._path == script  # pyright: ignore[reportPrivateUsage]
    for shipped in ("gcp", "aws", "hetzner"):
        assert cloud_named(shipped)._path == SCRIPTS / shipped  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(CloudRefused, match="no cloud script"):
        cloud_named("nimbus")


def test_every_shipped_script_is_executable_and_names_its_three_verbs() -> None:
    for shipped in ("gcp", "aws", "hetzner"):
        path = SCRIPTS / shipped
        assert path.stat().st_mode & 0o111, f"{shipped} is not executable"
        text = path.read_text()
        assert all(verb in text for verb in ("create)", "delete)", "list)")), shipped
