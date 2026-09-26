"""The carriers table: one per org, replaced whole, sealed at rest, read back whole."""

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet

from pinecall._settings import Settings
from pinecall.log.store import Pool, open_pool
from pinecall.orgs.carriers import MemoryCarriers, PostgresCarriers, carriers_for
from pinecall.orgs.records import PostgresOrgs
from pinecall.types import Carrier, SipPeer, TwilioAccount
from tests.postgres import Dev

A_SID = "AC" + "0" * 32
TWILIO = TwilioAccount(A_SID, A_SID, "the-auth-token")
PEER = SipPeer("pbx", "pw", ("203.0.113.0/24", "198.51.100.7/32"))


@pytest.mark.unit
async def test_the_memory_table_keeps_one_carrier_per_org_and_reads_the_credentials_back() -> None:
    cipher = Fernet(Fernet.generate_key())
    carriers = MemoryCarriers(cipher)
    await carriers.put(Carrier("clinica", TWILIO))
    assert await carriers.of("clinica") == Carrier("clinica", TWILIO)
    await carriers.put(Carrier("clinica", PEER))
    assert await carriers.of("clinica") == Carrier("clinica", PEER)
    assert await carriers.of("tienda") is None
    assert await carriers.drop("clinica") is True
    assert await carriers.drop("clinica") is False


@pytest.mark.unit
def test_no_vault_key_means_no_carriers_table_at_all() -> None:
    assert carriers_for(Settings(world="production", vault_key=None), pool=None) is None
    assert isinstance(
        carriers_for(
            Settings(world="production", vault_key=Fernet.generate_key().decode()), pool=None
        ),
        MemoryCarriers,
    )


@pytest.fixture
async def pool(postgres: Dev) -> AsyncIterator[Pool]:
    pool = await open_pool(postgres.dsn, schema=postgres.schema)
    try:
        yield pool
    finally:
        await pool.close()


@pytest.mark.postgres
async def test_the_postgres_row_holds_a_token_and_never_the_secret(pool: Pool) -> None:
    slug = f"org-{uuid4().hex[:12]}"
    org = await PostgresOrgs(pool).create(slug, slug)
    assert org is not None
    cipher = Fernet(Fernet.generate_key())
    carriers = PostgresCarriers(pool, cipher)
    await carriers.put(Carrier(org.id, TWILIO))
    row = await pool.fetchrow(
        "select kind, account, ciphertext from carriers where org = $1", org.id
    )
    assert row is not None and (row["kind"], row["account"]) == ("twilio", A_SID)
    assert "the-auth-token" not in str(row["ciphertext"])
    assert await carriers.of(org.id) == Carrier(org.id, TWILIO)
    await carriers.put(Carrier(org.id, PEER))
    assert await carriers.of(org.id) == Carrier(org.id, PEER)
    assert await carriers.drop(org.id) is True
    assert await carriers.of(org.id) is None
