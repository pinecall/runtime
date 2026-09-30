"""Tests for a recording's own key: made once per call, read back while the vault opens it."""

from cryptography.fernet import Fernet

from pinecall.postgres.pool import Pool
from pinecall.process.connections import vault_of
from pinecall.tenancy.recording_keys import key_for, key_of
from tests.conftest import postgres

pytestmark = postgres

KEY = Fernet.generate_key().decode()


async def test_a_call_has_one_key_however_often_its_worker_asks(pool: Pool) -> None:
    vault = vault_of(KEY)
    first = await key_for(pool, vault, "org_1", "CA_1")
    assert first is not None
    assert len(first) == 32
    assert await key_for(pool, vault, "org_1", "CA_1") == first
    assert await key_of(pool, vault, "CA_1") == first
    assert await key_for(pool, vault, "org_1", "CA_2") != first


async def test_a_call_with_no_key_or_one_the_vault_no_longer_opens_reads_as_none(
    pool: Pool,
) -> None:
    await key_for(pool, vault_of(KEY), "org_1", "CA_1")
    assert await key_of(pool, vault_of(KEY), "CA_never") is None
    elsewhere = vault_of(Fernet.generate_key().decode())
    assert await key_of(pool, elsewhere, "CA_1") is None
    assert await key_for(pool, elsewhere, "org_1", "CA_1") is None
