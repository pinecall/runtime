"""Tests for an org's telemetry row: sealed headers, read back by name, dropped."""

from cryptography.fernet import Fernet, MultiFernet

from pinecall.domain.telemetry import Telemetry
from pinecall.postgres.pool import Pool
from pinecall.tenancy.telemetry import described, drop_telemetry, put_telemetry, telemetry_of
from tests.conftest import postgres

VAULT = MultiFernet([Fernet(Fernet.generate_key())])
A_COLLECTOR = Telemetry("https://otel.example.test/v1/traces", {"x-api-key": "made-up"}, pii=True)


@postgres
async def test_the_collector_is_kept_with_its_headers_sealed_and_read_back_by_name(
    pool: Pool,
) -> None:
    await put_telemetry(pool, VAULT, "org_1", A_COLLECTOR)
    assert await telemetry_of(pool, VAULT, "org_1") == A_COLLECTOR
    named = await described(pool, VAULT, "org_1")
    assert named is not None
    assert (named.endpoint, named.header_names, named.pii) == (
        A_COLLECTOR.endpoint,
        ("x-api-key",),
        True,
    )
    async with pool.connection() as connection:
        row = await (await connection.execute("SELECT ciphertext FROM org_telemetry")).fetchone()
    assert row is not None
    assert "made-up" not in str(row["ciphertext"])


@postgres
async def test_an_org_sending_traces_nowhere_reads_none_and_a_drop_says_whether_it_did(
    pool: Pool,
) -> None:
    assert await telemetry_of(pool, VAULT, "org_2") is None
    assert not await drop_telemetry(pool, "org_2")
    await put_telemetry(pool, VAULT, "org_2", Telemetry("https://otel.example.test"))
    assert await drop_telemetry(pool, "org_2")
    assert await described(pool, VAULT, "org_2") is None
