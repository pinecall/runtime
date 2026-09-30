"""Tests for what a process opens: the vault key checked first, and everything closed after."""

import pytest
from cryptography.fernet import Fernet

from pinecall.domain.errors import NotAvailable, SettingsRefused
from pinecall.process.connections import keyring_of, opened, server_of, vault_of
from pinecall.process.settings import Settings
from tests.conftest import DSN, postgres, settings_of


def test_a_process_with_no_vault_key_does_not_start_and_says_which_variable() -> None:
    with pytest.raises(SettingsRefused, match="PINECALL_VAULT_KEY"):
        vault_of(None)


def test_a_list_with_a_key_that_is_not_one_is_refused_without_repeating_the_keys() -> None:
    good = Fernet.generate_key().decode()
    with pytest.raises(SettingsRefused, match="PINECALL_VAULT_KEY") as refused:
        vault_of(f"{good},not-a-key")
    assert good not in str(refused.value)


def test_the_first_key_seals_and_every_key_listed_opens() -> None:
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    sealed_under_old = vault_of(old).encrypt(b"secret")
    assert vault_of(f"{new},{old}").decrypt(sealed_under_old) == b"secret"


def test_the_keyring_is_the_list_in_its_order_and_an_empty_one_is_refused() -> None:
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    first, second = keyring_of(f" {new} , {old} ")
    assert first.decrypt(Fernet(new).encrypt(b"x")) == b"x"
    assert second.decrypt(Fernet(old).encrypt(b"x")) == b"x"
    with pytest.raises(SettingsRefused, match="not a Fernet key"):
        keyring_of(" , ")


def test_a_process_with_no_livekit_pair_reaches_no_sfu() -> None:
    with pytest.raises(NotAvailable, match="LIVEKIT_API_KEY"):
        server_of(Settings.model_validate({"LIVEKIT_URL": "ws://127.0.0.1:9"}))


@postgres
async def test_the_connections_open_on_the_settings_and_close_after() -> None:
    settings = settings_of().model_copy(
        update={"database_url": DSN, "vault_key": Fernet.generate_key().decode()}
    )
    async with opened(settings) as connections, connections.pool.connection() as connection:
        assert (await (await connection.execute("select 1")).fetchone()) is not None
    assert connections.pool.closed


@postgres
async def test_the_pool_is_as_large_as_the_settings_say() -> None:
    settings = settings_of().model_copy(
        update={"database_url": DSN, "vault_key": Fernet.generate_key().decode(), "db_pool": 3}
    )
    async with opened(settings) as connections:
        assert connections.pool.max_size == 3
