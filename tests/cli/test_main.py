"""Tests for pinecall-runtime: the verbs, the gateway's bind, the doctor, the fleet key."""

import argparse

import pytest

from pinecall.cli.main import doctor, fleet_key, gateway, main, migrate_up
from pinecall.domain.errors import PinecallError
from pinecall.domain.settings import Settings
from tests.conftest import DSN, postgres


def test_a_verb_nobody_declared_is_refused_with_the_list() -> None:
    with pytest.raises(SystemExit) as refused:
        main(["nothing"])
    assert refused.value.code == 2


def test_the_gateway_refuses_to_bind_anything_but_loopback() -> None:
    settings = Settings.model_validate({"PINECALL_GATEWAY_URL": "http://0.0.0.0:8080"})
    with pytest.raises(PinecallError, match="loopback"):
        gateway(settings, argparse.Namespace())


def test_the_doctor_says_each_missing_thing_and_exits_one(
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = Settings.model_validate(
        {
            "DATABASE_URL": "postgresql://nobody@127.0.0.1:1/none",
            "PINECALL_GATEWAY_URL": "http://127.0.0.1:1",
        }
    )
    assert doctor(settings, argparse.Namespace()) == 1
    said = capsys.readouterr().out.splitlines()
    assert [line.split()[1].rstrip(":") for line in said] == [
        "vault",
        "database",
        "livekit",
        "gateway",
    ]
    assert all(line.startswith("NO") for line in said)


@postgres
def test_a_migrated_database_mints_a_fleet_key_printed_once_and_nothing_else(
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = Settings.model_validate({"DATABASE_URL": DSN})
    assert migrate_up(settings, argparse.Namespace()) == 0
    capsys.readouterr()
    assert fleet_key(settings, argparse.Namespace(env="sandbox")) == 0
    printed = capsys.readouterr().out
    assert printed.startswith("pc_test_")
    assert "\n" not in printed
