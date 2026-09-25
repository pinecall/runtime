"""`box instance <name>`: exactly one env file per instance, its defaults, and what it refuses."""

from io import StringIO
from pathlib import Path

import pytest

from pinecall._settings import NOBODY_TO_ASK
from pinecall.cli import build_parser
from pinecall.cli.box.instance import (
    ALREADY,
    NOT_A_NAME,
    PORT_TAKEN,
    InstanceRefused,
    credstore_of,
    database_of,
    declared,
    env_file,
    write_instance,
)

pytestmark = pytest.mark.unit

PRODUCTION_URL = "https://box.example.com"


def written(instances: Path, *argv: str) -> str:
    """`box instance …` run into this directory, the recordings kept nowhere; the file it wrote."""
    arguments = build_parser().parse_args(["box", "instance", *argv, "--into", str(instances)])
    kept: list[Path] = []
    instance = declared(
        arguments.name,
        arguments.world,
        arguments.domain,
        instances,
        port=arguments.port,
        fleet=arguments.fleet,
        identity=arguments.identity,
        elsewhere=arguments.elsewhere,
        sandbox=arguments.sandbox,
        max_jobs=arguments.max_jobs,
        idle_processes=arguments.idle_processes,
    )
    write_instance(
        instance, instances, force=arguments.force, out=StringIO(), keep_recordings=kept.append
    )
    assert kept == [Path("/var/lib/pinecall/recordings") / arguments.name]
    return env_file(arguments.name, instances).read_text()


def test_the_first_instance_is_production_on_8080_with_the_default_fleet(tmp_path: Path) -> None:
    said = written(tmp_path, "production", "--world", "production", "--domain", "box.example.com")
    assert said.splitlines() == [
        "# The instance production, written by `pinecall-runtime box instance`.",
        "PINECALL_WORLD=production",
        "PINECALL_FLEET=pinecall",
        "PINECALL_DOMAIN=box.example.com",
        "PINECALL_GATEWAY_URL=http://127.0.0.1:8080",
        "PINECALL_WORKER_HTTP_PORT=8082",
        "PINECALL_RECORDINGS=/var/lib/pinecall/recordings/production",
        "PINECALL_IDENTITY_URL=",
        "PINECALL_ELSEWHERE_URL=",
        "PINECALL_SANDBOX_URL=",
        "PINECALL_MAX_JOBS=",
        "PINECALL_IDLE_PROCESSES=",
    ]


def test_a_sandbox_beside_it_takes_the_next_hundred_and_a_fleet_of_its_own(
    tmp_path: Path,
) -> None:
    written(tmp_path, "production", "--world", "production", "--domain", "box.example.com")
    said = written(
        tmp_path,
        "sandbox",
        "--world",
        "sandbox",
        "--domain",
        "sandbox.example.com",
        "--identity",
        PRODUCTION_URL,
        "--elsewhere",
        PRODUCTION_URL,
        "--idle-processes",
        "1",
    )
    assert "PINECALL_WORLD=sandbox" in said.splitlines()
    assert "PINECALL_FLEET=pinecall-sandbox" in said.splitlines()
    assert "PINECALL_GATEWAY_URL=http://127.0.0.1:8180" in said.splitlines()
    assert "PINECALL_WORKER_HTTP_PORT=8182" in said.splitlines()
    assert f"PINECALL_IDENTITY_URL={PRODUCTION_URL}" in said.splitlines()
    assert f"PINECALL_ELSEWHERE_URL={PRODUCTION_URL}" in said.splitlines()
    assert "PINECALL_IDLE_PROCESSES=1" in said.splitlines()


# Production names its sandbox once, in its own file: the URL it asks whose a ring is.
def test_production_names_the_sandbox_it_asks_about_a_developers_phone(tmp_path: Path) -> None:
    said = written(
        tmp_path,
        "production",
        "--world",
        "production",
        "--domain",
        "box.example.com",
        "--sandbox",
        "https://sandbox.example.com",
    )
    assert "PINECALL_SANDBOX_URL=https://sandbox.example.com" in said.splitlines()


def test_a_third_skips_every_hundred_already_held(tmp_path: Path) -> None:
    written(tmp_path, "production", "--world", "production", "--domain", "a.example.com")
    written(tmp_path, "staging", "--world", "production", "--domain", "b.example.com")
    said = written(tmp_path, "demo", "--world", "production", "--domain", "c.example.com")
    assert "PINECALL_GATEWAY_URL=http://127.0.0.1:8280" in said.splitlines()


def test_a_port_another_instance_holds_is_refused_by_its_owner(tmp_path: Path) -> None:
    written(tmp_path, "production", "--world", "production", "--domain", "a.example.com")
    with pytest.raises(InstanceRefused) as refused:
        declared("staging", "production", "b.example.com", tmp_path, port=8078)
    assert str(refused.value) == PORT_TAKEN.format(port=8078, other="production")


@pytest.mark.parametrize("name", ["Sandbox", "-sandbox", "sand_box", "a" * 33, ""])
def test_a_name_that_is_not_a_slug_is_refused(tmp_path: Path, name: str) -> None:
    with pytest.raises(InstanceRefused) as refused:
        declared(name, "production", "a.example.com", tmp_path)
    assert str(refused.value) == NOT_A_NAME.format(name=name)


def test_a_sandbox_with_nobody_to_ask_is_refused_before_it_is_a_box_that_cannot_start(
    tmp_path: Path,
) -> None:
    with pytest.raises(InstanceRefused) as refused:
        declared("sandbox", "sandbox", "sandbox.example.com", tmp_path)
    assert str(refused.value) == NOBODY_TO_ASK


def test_a_file_that_is_there_is_never_written_over_unasked(tmp_path: Path) -> None:
    written(tmp_path, "production", "--world", "production", "--domain", "a.example.com")
    with pytest.raises(InstanceRefused) as refused:
        written(tmp_path, "production", "--world", "production", "--domain", "b.example.com")
    assert str(refused.value) == ALREADY.format(path=env_file("production", tmp_path))
    assert "PINECALL_DOMAIN=a.example.com" in env_file("production", tmp_path).read_text()

    said = written(
        tmp_path, "production", "--world", "production", "--domain", "b.example.com", "--force"
    )
    assert "PINECALL_DOMAIN=b.example.com" in said.splitlines()
    assert "PINECALL_GATEWAY_URL=http://127.0.0.1:8080" in said.splitlines()


def test_an_instance_keeps_its_secrets_beside_its_file_and_outside_the_boxs_credstore() -> None:
    assert env_file("sandbox") == Path("/etc/pinecall/instances/sandbox.env")
    assert credstore_of("sandbox") == Path("/etc/pinecall/instances/sandbox.credstore")
    assert database_of("staging-eu") == "pinecall_staging_eu"
