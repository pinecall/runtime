"""~/.pinecall/dev: written by a gateway on a dev key, and by nothing else. Never the real home."""

import json
from pathlib import Path

import pytest

from pinecall._settings import Settings
from pinecall.cli import dev_file

pytestmark = pytest.mark.unit

A_DEV_KEY = "a-dev-key-nobody-will-ever-deploy"


@pytest.fixture(autouse=True)
def a_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """This test's own ~/.pinecall. The machine's is neither read nor written by anything here."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    # Settings() reads the shell: an exported dev key must not turn "a box" into a laptop.
    monkeypatch.delenv("PINECALL_DEV_KEY", raising=False)
    return home / ".pinecall"


def test_a_gateway_on_a_dev_key_says_where_it_is_and_what_it_takes(a_home: Path) -> None:
    written = dev_file.written(Settings(dev_key=A_DEV_KEY), 8099)

    assert written is not None
    assert written == a_home / "dev"
    assert json.loads(written.read_text()) == {"url": "http://127.0.0.1:8099", "key": A_DEV_KEY}


def test_the_file_is_readable_by_nobody_else(a_home: Path) -> None:
    written = dev_file.written(Settings(dev_key=A_DEV_KEY), 8080)

    assert written is not None
    assert written.stat().st_mode & 0o777 == 0o600
    assert a_home.stat().st_mode & 0o777 == 0o700


def test_a_box_writes_nothing_at_all(a_home: Path) -> None:
    assert dev_file.written(Settings(), 8080) is None
    assert not a_home.exists()


# A gateway that used to run on this machine and now runs on issued keys leaves a file that says
# a dev key opens it. It does not, so the file is a lie, and the next process takes it away.
def test_a_gateway_with_no_dev_key_takes_a_stale_file_away(a_home: Path) -> None:
    dev_file.written(Settings(dev_key=A_DEV_KEY), 8080)

    assert dev_file.written(Settings(), 8080) is None
    assert not (a_home / "dev").exists()
