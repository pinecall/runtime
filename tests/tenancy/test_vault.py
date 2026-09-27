"""The vault: one key list seals every secret, and a vendor's credentials are kept only sealed."""

import logging

import pytest
from cryptography.fernet import Fernet

from pinecall.domain.errors import SettingsRefused
from pinecall.domain.types import JsonObject
from pinecall.postgres.pool import Pool
from pinecall.tenancy.orgs import FLEETS, Fleets, create, remove, set_fleets
from pinecall.tenancy.vault import (
    VARIABLE,
    box_credentials,
    credentials_of,
    drop_box_credentials,
    drop_credentials,
    opened,
    put_box_credentials,
    put_credentials,
    sealed,
    vault_of,
    vendors_of,
)
from tests.conftest import postgres

OLD = Fernet.generate_key().decode()
NEW = Fernet.generate_key().decode()
AZURE: JsonObject = {"speech_key": "made-up-by-this-test", "speech_region": "westeurope"}


def test_a_gateway_with_no_vault_key_does_not_start_and_says_which_variable() -> None:
    with pytest.raises(SettingsRefused, match=VARIABLE):
        vault_of(None)


def test_a_list_with_a_key_that_is_not_one_is_refused_naming_the_variable() -> None:
    with pytest.raises(SettingsRefused, match=VARIABLE) as refused:
        vault_of(f"{NEW},not-a-key")
    assert NEW not in str(refused.value)


def test_a_secret_sealed_under_the_old_key_opens_once_the_new_one_is_in_front() -> None:
    token = sealed(vault_of(OLD), AZURE)
    assert opened(vault_of(f"{NEW}, {OLD}"), token) == AZURE


def test_a_secret_sealed_under_a_key_no_longer_listed_reads_as_unset_and_says_so(
    caplog: pytest.LogCaptureFixture,
) -> None:
    token = sealed(vault_of(OLD), "a-secret")
    with caplog.at_level(logging.WARNING):
        assert opened(vault_of(NEW), token) is None
    assert VARIABLE in caplog.text


def test_a_sealed_secret_carries_nothing_of_the_secret() -> None:
    assert "made-up-by-this-test" not in sealed(vault_of(NEW), AZURE)


@postgres
async def test_an_orgs_credentials_round_trip_and_the_secret_is_not_in_the_row(
    pool: Pool,
) -> None:
    vault = vault_of(NEW)
    org = await create(pool, "clinica-norte", "Clínica Norte")
    await put_credentials(pool, vault, org.id, "azure", AZURE)
    await put_credentials(pool, vault, org.id, "deepgram", "dg-made-up")
    assert await credentials_of(pool, vault, org.id) == {"azure": AZURE, "deepgram": "dg-made-up"}
    assert await vendors_of(pool, org.id) == ["azure", "deepgram"]
    async with pool.connection() as connection:
        rows = await (await connection.execute("SELECT ciphertext FROM provider_keys")).fetchall()
    assert all("made-up" not in row["ciphertext"] for row in rows)


@postgres
async def test_one_key_per_org_and_vendor_and_a_second_one_replaces_it(pool: Pool) -> None:
    vault = vault_of(NEW)
    org = await create(pool, "clinica-norte", "Clínica Norte")
    other = await create(pool, "northwind", "Northwind")
    await put_credentials(pool, vault, org.id, "deepgram", "first")
    await put_credentials(pool, vault, org.id, "deepgram", "second")
    assert await credentials_of(pool, vault, org.id) == {"deepgram": "second"}
    assert await credentials_of(pool, vault, other.id) == {}


@postgres
async def test_dropping_credentials_nobody_kept_is_false_and_not_an_error(pool: Pool) -> None:
    vault = vault_of(NEW)
    org = await create(pool, "clinica-norte", "Clínica Norte")
    await put_credentials(pool, vault, org.id, "deepgram", "dg")
    assert await drop_credentials(pool, org.id, "deepgram")
    assert not await drop_credentials(pool, org.id, "deepgram")
    assert await credentials_of(pool, vault, org.id) == {}


@postgres
async def test_removing_the_org_takes_its_keys_with_it(pool: Pool) -> None:
    vault = vault_of(NEW)
    org = await create(pool, "clinica-norte", "Clínica Norte")
    await put_credentials(pool, vault, org.id, "deepgram", "dg")
    await remove(pool, org.id)
    assert await vendors_of(pool, org.id) == []


@postgres
async def test_a_stale_row_leaves_that_vendor_out_and_the_others_in(pool: Pool) -> None:
    org = await create(pool, "clinica-norte", "Clínica Norte")
    await put_credentials(pool, vault_of(OLD), org.id, "deepgram", "sealed-long-ago")
    await put_credentials(pool, vault_of(NEW), org.id, "cartesia", "sealed-now")
    assert await credentials_of(pool, vault_of(NEW), org.id) == {"cartesia": "sealed-now"}


@postgres
async def test_the_box_offers_a_vendor_by_holding_its_key_beside_its_other_settings(
    pool: Pool,
) -> None:
    vault = vault_of(NEW)
    await set_fleets(pool, Fleets())
    await put_box_credentials(pool, vault, "cartesia", "box-made-up")
    await put_box_credentials(pool, vault, "cartesia", "box-rotated")
    await put_box_credentials(pool, vault, "azure", AZURE)
    assert await box_credentials(pool, vault) == {"azure": AZURE, "cartesia": "box-rotated"}
    assert await drop_box_credentials(pool, "azure")
    assert not await drop_box_credentials(pool, "azure")
    assert await box_credentials(pool, vault) == {"cartesia": "box-rotated"}
    async with pool.connection() as connection:
        names = await (await connection.execute("SELECT name FROM box_settings")).fetchall()
    assert FLEETS in {row["name"] for row in names}
