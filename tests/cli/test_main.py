"""Tests for pinecall-runtime: the verbs, the gateway's bind, the doctor, the fleet key."""

import argparse
import asyncio
from functools import partial
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from pinecall.cli import main as cli
from pinecall.cli._operator import fleet_key, runner_key
from pinecall.cli.main import (
    doctor,
    gateway,
    main,
    migrate_plan,
    migrate_status,
    migrate_up,
    providers_prices,
    providers_seed,
    retention_due,
    retention_run,
    vault_rotate,
)
from pinecall.domain.errors import Conflict, PinecallError
from pinecall.log.store import Store
from pinecall.postgres.pool import open_pool
from pinecall.process.connections import vault_of
from pinecall.process.settings import Settings
from pinecall.tenancy import orgs, policy, vault
from pinecall.wire.rest.accounts import OrgPolicy
from tests.conftest import DSN, configured, postgres
from tests.log.conftest import logged_call


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
        "facts",
        "days",
        "archive",
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


@postgres
def test_a_migrated_database_mints_a_runner_key_printed_once_and_nothing_else(
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = Settings.model_validate({"DATABASE_URL": DSN})
    assert migrate_up(settings, argparse.Namespace()) == 0
    capsys.readouterr()
    assert runner_key(settings, argparse.Namespace(env="production")) == 0
    printed = capsys.readouterr().out
    assert printed.startswith("pc_live_")
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


@postgres
async def test_retention_says_what_is_due_then_erases_it_and_says_how_many(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    schema: str,
) -> None:
    monkeypatch.setattr(cli, "open_pool", partial(open_pool, schema=schema))
    pool = await open_pool(DSN, schema=schema)
    try:
        org = await orgs.create(pool, "clinica", "Clinica")
        call = await logged_call(Store(pool, clock=lambda: 1.0), org.id)
        await policy.put_policy(pool, org.id, OrgPolicy(retention_days=1), by="m_1")
    finally:
        await pool.close()
    settings = Settings.model_validate({"DATABASE_URL": DSN, "PINECALL_RECORDINGS": str(tmp_path)})
    capsys.readouterr()
    assert await asyncio.to_thread(retention_due, settings, argparse.Namespace()) == 0
    listed = capsys.readouterr().out
    assert call in listed
    assert listed.endswith("1 calls past their org's days\n")
    assert await asyncio.to_thread(retention_run, settings, argparse.Namespace()) == 0
    ran = capsys.readouterr().out.splitlines()
    assert ran[0] == "1 calls erased past their org's days"
    # The call started in 1970 by the store's clock, so its record is past 24 months at once.
    assert ran[1].endswith("dials forgotten past 24 months")
    assert ran[2] == "0 WhatsApp message ids forgotten past Meta's 7 days of retries"
    assert ran[3].startswith("days of the log: ")
    assert await asyncio.to_thread(retention_due, settings, argparse.Namespace()) == 0
    assert capsys.readouterr().out == "0 calls past their org's days\n"


@postgres
async def test_vault_rotate_reseals_under_the_first_key_and_fails_while_a_row_opens_under_none(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    schema: str,
) -> None:
    monkeypatch.setattr(cli, "open_pool", partial(open_pool, schema=schema))
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    pool = await open_pool(DSN, schema=schema)
    try:
        await vault.put_box_credentials(pool, vault_of(old), "cartesia", "box-made-up")
        settings = Settings.model_validate({"DATABASE_URL": DSN, "PINECALL_VAULT_KEY": new})
        capsys.readouterr()
        refused = await asyncio.to_thread(vault_rotate, settings, argparse.Namespace())
        unopened = capsys.readouterr().out
        rotating = Settings.model_validate(
            {"DATABASE_URL": DSN, "PINECALL_VAULT_KEY": f"{new},{old}"}
        )
        rotated = await asyncio.to_thread(vault_rotate, rotating, argparse.Namespace())
        printed = capsys.readouterr().out
        assert await vault.box_credentials(pool, vault_of(new)) == {"cartesia": "box-made-up"}
    finally:
        await pool.close()
    assert (refused, rotated) == (1, 0)
    assert "box_settings.ciphertext: 0 re-sealed, 0 under the first key already, 1 opened" in (
        unopened
    )
    assert (
        "box_settings.ciphertext: 1 re-sealed, 0 under the first key already, 0 opened" in printed
    )
    assert len(printed.splitlines()) == len(vault.SEALED_COLUMNS)
