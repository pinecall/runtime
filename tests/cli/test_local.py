"""Tests for `local`: the files it writes, the settings drawn once, the refusal without Docker."""

import argparse
import stat
from pathlib import Path

import pytest

from pinecall.cli import _local
from pinecall.cli.main import verbs
from pinecall.domain.errors import PinecallError
from pinecall.process.settings import Settings


def test_the_compose_files_are_written_and_the_settings_drawn_once(tmp_path: Path) -> None:
    compose = _local.written(tmp_path)

    assert compose == tmp_path / "compose.yaml"
    assert {str(file.relative_to(tmp_path)) for file in tmp_path.rglob("*") if file.is_file()} == {
        *_local.SHIPPED,
        "env",
    }
    env = tmp_path / "env"
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    first = env.read_text(encoding="utf-8")
    assert "DATABASE_URL=postgresql://pinecall:pinecall@127.0.0.1:55433/pinecall" in first
    assert "PINECALL_OPS_KEY=pc_ops_" in first
    assert (tmp_path / "recordings").is_dir()

    _local.written(tmp_path)

    assert env.read_text(encoding="utf-8") == first, "the secrets are drawn once"


def test_the_settings_file_is_read_as_the_runtime_reads_it(tmp_path: Path) -> None:
    _local.written(tmp_path)

    values = _local.read(tmp_path / "env")
    settings = _local.settings_of(values)

    assert settings.database_url.endswith("@127.0.0.1:55433/pinecall")
    assert settings.gateway_url == _local.GATEWAY_URL
    assert values["PINECALL_OPS_KEY"].startswith("pc_ops_")
    assert settings.ops_key == values["PINECALL_OPS_KEY"]


def test_up_without_docker_is_refused_in_one_sentence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def nothing(_name: str) -> None:
        return None

    monkeypatch.setattr(_local, "which", nothing)
    args = argparse.Namespace(dir=str(tmp_path), services_only=True)

    with pytest.raises(PinecallError, match="Docker is not here"):
        _local.local_up(Settings.model_validate({}), args)


def test_the_verbs_parse_with_their_directory() -> None:
    parsed = verbs().parse_args(["local", "--dir", "/var/pinecall/x", "up", "--services-only"])

    assert (parsed.dir, parsed.services_only) == ("/var/pinecall/x", True)
    assert parsed.run is _local.local_up
    assert verbs().parse_args(["local", "down", "--volumes"]).volumes is True
    assert verbs().parse_args(["local", "init", "--email", "a@b.c", "--person", "A"]).person == "A"


def test_a_service_that_never_answers_its_port_is_said_by_name() -> None:
    with pytest.raises(PinecallError, match=r"Postgres did not answer on 127\.0\.0\.1:9"):
        _local.answering("Postgres", 9, within_s=0.2)
