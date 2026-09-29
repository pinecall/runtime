"""Tests for pinecall-runtime: the verbs, the gateway's bind, the doctor, the fleet key."""

import argparse
import asyncio
from functools import partial
from pathlib import Path

import pytest

from pinecall.cli import main as cli
from pinecall.cli._operator import fleet_key
from pinecall.cli.main import (
    doctor,
    gateway,
    main,
    migrate_plan,
    migrate_status,
    migrate_up,
    providers_prices,
    providers_seed,
)
from pinecall.domain.errors import Conflict, PinecallError
from pinecall.postgres.pool import open_pool
from pinecall.process.settings import Settings
from tests.conftest import DSN, configured, postgres


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
    data = capsys.readouterr().out.splitlines()
    assert [line.split()[1].rstrip(":") for line in data] == [
        "vault",
        "database",
        "livekit",
        "gateway",
    ]
    assert all(line.startswith("NO") for line in data)


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


def test_the_plan_names_every_migration_on_the_disk_in_order(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert migrate_plan(Settings.model_validate({}), argparse.Namespace()) == 0
    names = capsys.readouterr().out.splitlines()
    assert names[0] == "0001_schema.sql"
    assert names == sorted(names)


@postgres
def test_the_status_says_up_to_date_once_up_is_run(capsys: pytest.CaptureFixture[str]) -> None:
    settings = Settings.model_validate({"DATABASE_URL": DSN})
    migrate_up(settings, argparse.Namespace())
    capsys.readouterr()
    assert migrate_status(settings, argparse.Namespace()) == 0
    assert capsys.readouterr().out == "up to date\n"


# On the test's own schema, so a second run of the suite finds no row.
@postgres
async def test_the_providers_row_is_seeded_once_and_the_second_time_refused(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    schema: str,
    acme: str,
) -> None:
    del acme
    monkeypatch.setattr(cli, "open_pool", partial(open_pool, schema=schema))
    settings = Settings.model_validate({"DATABASE_URL": DSN})
    row = tmp_path / "providers.json"
    row.write_text(configured().model_dump_json())
    capsys.readouterr()
    seeding = argparse.Namespace(file=str(row))
    first = await asyncio.to_thread(providers_seed, settings, seeding)
    assert (first, "is written" in capsys.readouterr().out) == (0, True)
    with pytest.raises(Conflict, match="configured already"):
        await asyncio.to_thread(providers_seed, settings, seeding)


@postgres
async def test_a_prices_file_is_shown_until_applied_and_then_joins_the_boxs_rates(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    schema: str,
    acme: str,
) -> None:
    del acme
    monkeypatch.setattr(cli, "open_pool", partial(open_pool, schema=schema))
    settings = Settings.model_validate({"DATABASE_URL": DSN})
    row = tmp_path / "providers.json"
    row.write_text(configured().model_dump_json())
    await asyncio.to_thread(providers_seed, settings, argparse.Namespace(file=str(row)))
    prices_file = tmp_path / "prices.csv"
    prices_file.write_text(
        "vendor,model,unit,usd,as_of,source\nacme,acme-voice,characters,0.00005,2026-09-29,s\n"
    )
    capsys.readouterr()
    shown = argparse.Namespace(file=str(prices_file), apply=False)
    await asyncio.to_thread(providers_prices, settings, shown)
    first = capsys.readouterr().out
    applied = argparse.Namespace(file=str(prices_file), apply=True)
    await asyncio.to_thread(providers_prices, settings, applied)
    await asyncio.to_thread(providers_prices, settings, shown)
    last = capsys.readouterr().out.splitlines()[-2]
    assert "+ acme-voice" in first
    assert "nothing written" in first
    assert last == "0 new, 0 changed, 1 the same, 1 only on the box and kept"
