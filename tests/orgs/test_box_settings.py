"""The box_settings table in memory: a row a setting, its one secret sealed, its value merged."""

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet

from pinecall._settings import Settings
from pinecall.log.store import Pool, open_pool
from pinecall.orgs.box import (
    BoxSetting,
    MemoryBoxSettings,
    PostgresBoxSettings,
    box_settings_for,
)
from pinecall.orgs.vault import NoVaultKey
from tests.postgres import Dev

pytestmark = pytest.mark.unit

A_KEY = Fernet.generate_key().decode()


async def test_a_setting_is_kept_whole_read_back_with_its_secret_and_dropped_once() -> None:
    box = MemoryBoxSettings(Fernet(A_KEY.encode()))
    assert await box.of("mail") is None
    await box.put("mail", {"host": "smtp.example.com"}, "hunter2")
    assert await box.of("mail") == BoxSetting({"host": "smtp.example.com"}, "hunter2")
    await box.noted("mail", {"last_error": None, "verified_at": "2026-09-17T10:00:00+00:00"})
    kept = await box.of("mail")
    assert kept is not None and kept.value["verified_at"] and kept.secret == "hunter2"
    assert await box.drop("mail") is True and await box.drop("mail") is False
    await box.noted("mail", {"last_error": "late"})
    assert await box.of("mail") is None, "a note on nothing writes nothing"


async def test_a_secret_needs_the_vault_key_and_a_value_alone_does_not() -> None:
    box = MemoryBoxSettings(None)
    await box.put("brand", {"name": "Acme Voice"})
    assert await box.of("brand") == BoxSetting({"name": "Acme Voice"}, None)
    with pytest.raises(NoVaultKey):
        await box.put("mail", {"host": "smtp.example.com"}, "hunter2")


async def test_a_secret_sealed_under_another_key_reads_as_none_rather_than_dying() -> None:
    sealed = MemoryBoxSettings(Fernet(A_KEY.encode()))
    await sealed.put("mail", {"host": "smtp.example.com"}, "hunter2")
    # The same rows, opened with a key that is not the one that sealed them.
    sealed._cipher = Fernet(Fernet.generate_key())  # pyright: ignore[reportPrivateUsage]
    assert await sealed.of("mail") == BoxSetting({"host": "smtp.example.com"}, None)


def test_the_table_exists_without_a_vault_key_unlike_the_org_tables_beside_it() -> None:
    assert isinstance(box_settings_for(Settings(), None), MemoryBoxSettings)
    assert isinstance(box_settings_for(Settings(vault_key=A_KEY), None), MemoryBoxSettings)


@pytest.fixture
async def pool(postgres: Dev) -> AsyncIterator[Pool]:
    pool = await open_pool(postgres.dsn, schema=postgres.schema)
    try:
        yield pool
    finally:
        await pool.close()


@pytest.mark.postgres
async def test_the_postgres_row_holds_a_token_and_a_merge_and_walks_the_way_the_twin_does(
    pool: Pool,
) -> None:
    """0035's table, through the very SQL the gateway runs: sealed, merged, replaced, dropped."""
    box = PostgresBoxSettings(pool, Fernet(A_KEY.encode()))
    name = f"mail.{uuid4().hex[:8]}"
    assert await box.of(name) is None
    await box.put(name, {"host": "smtp.example.com", "port": 587}, "hunter2")
    row = await pool.fetchrow(
        "select value::text, ciphertext from box_settings where name = $1", name
    )
    assert row is not None and "hunter2" not in str(row["ciphertext"])
    assert await box.of(name) == BoxSetting({"host": "smtp.example.com", "port": 587}, "hunter2")
    await box.noted(name, {"last_error": "refused"})
    kept = await box.of(name)
    assert kept is not None and kept.value["last_error"] == "refused" and kept.value["port"] == 587
    await box.put(name, {"name": "Acme Voice"})
    assert await box.of(name) == BoxSetting({"name": "Acme Voice"}, None), "replaced whole"
    assert await box.drop(name) is True and await box.drop(name) is False
    await box.noted(name, {"x": 1})
    assert await box.of(name) is None
