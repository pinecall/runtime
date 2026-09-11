"""~/.pinecall/dev: written by a gateway on a dev key, read by a worker about to knock at it."""

import json
from pathlib import Path

import pytest

from pinecall.auth import dev_file
from pinecall.auth.dev_file import Door

pytestmark = pytest.mark.unit

A_DEV_KEY = "a-dev-key-nobody-will-ever-deploy"


@pytest.fixture(autouse=True)
def a_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """This test's own ~/.pinecall. The machine's is neither read nor written by anything here."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    return home / ".pinecall"


def test_a_gateway_on_a_dev_key_says_where_it_is_and_what_it_takes(a_home: Path) -> None:
    written = dev_file.written(A_DEV_KEY, 8099)

    assert written is not None
    assert written == a_home / "dev"
    assert json.loads(written.read_text()) == {"url": "http://127.0.0.1:8099", "key": A_DEV_KEY}


def test_the_file_is_readable_by_nobody_else(a_home: Path) -> None:
    written = dev_file.written(A_DEV_KEY, 8080)

    assert written is not None
    assert written.stat().st_mode & 0o777 == 0o600
    assert a_home.stat().st_mode & 0o777 == 0o700


def test_a_box_writes_nothing_at_all(a_home: Path) -> None:
    assert dev_file.written(None, 8080) is None
    assert not a_home.exists()


# A gateway that used to run on this machine and now runs on issued keys leaves a file that says
# a dev key opens it. It does not, so the file is a lie, and the next process takes it away.
def test_a_gateway_with_no_dev_key_takes_a_stale_file_away(a_home: Path) -> None:
    dev_file.written(A_DEV_KEY, 8080)

    assert dev_file.written(None, 8080) is None
    assert not (a_home / "dev").exists()


def test_what_a_gateway_wrote_is_what_a_worker_finds() -> None:
    dev_file.written(A_DEV_KEY, 8099)

    assert dev_file.found() == Door(url="http://127.0.0.1:8099", key=A_DEV_KEY)


def test_no_file_is_no_door() -> None:
    assert dev_file.found() is None


def test_a_file_that_is_not_a_door_is_no_door_either(a_home: Path) -> None:
    """Half a door — a url with no key, or something that is not JSON — is the same as none."""
    a_home.mkdir(parents=True)
    (a_home / "dev").write_text('{"url": "http://127.0.0.1:8080"}')
    assert dev_file.found() is None

    (a_home / "dev").write_text("not json at all")
    assert dev_file.found() is None


# The tenant's CLI compares origin and path with no trailing slash (cli/credentials.ts:normalised),
# and the worker reads the same file, so it has to agree on which spellings are one gateway.
def test_a_door_knows_its_own_url_however_it_is_spelled() -> None:
    door = Door(url="http://127.0.0.1:8080", key=A_DEV_KEY)

    assert door.is_at("http://127.0.0.1:8080")
    assert door.is_at("http://127.0.0.1:8080/")
    assert not door.is_at("http://127.0.0.1:8081")
    assert not door.is_at("https://gateway.example.com")
