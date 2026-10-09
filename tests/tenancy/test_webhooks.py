"""Tests for an org's webhook row: the secret sealed, read back, dropped."""

from cryptography.fernet import Fernet, MultiFernet

from pinecall.domain.webhook import Webhook
from pinecall.postgres.pool import Pool
from pinecall.tenancy.webhooks import drop_webhook, put_webhook, webhook_of
from tests.conftest import postgres
from tests.tenancy.conftest import an_org

VAULT = MultiFernet([Fernet(Fernet.generate_key())])
A_URL = "https://hooks.example.test/alerts"


@postgres
async def test_the_secret_is_sealed_in_the_row_and_opened_on_the_way_back(pool: Pool) -> None:
    org = (await an_org(pool)).id
    await put_webhook(pool, VAULT, org, Webhook(A_URL, "shh"))
    async with pool.connection() as connection:
        row = await (await connection.execute("select * from org_webhooks")).fetchone()
    assert row is not None
    assert "shh" not in str(row["ciphertext"])
    assert await webhook_of(pool, VAULT, org) == Webhook(A_URL, "shh")


@postgres
async def test_a_webhook_without_a_secret_is_kept_replaced_and_dropped(pool: Pool) -> None:
    org = (await an_org(pool)).id
    assert await webhook_of(pool, VAULT, org) is None
    await put_webhook(pool, VAULT, org, Webhook(A_URL, "shh"))
    await put_webhook(pool, VAULT, org, Webhook(A_URL))
    assert await webhook_of(pool, VAULT, org) == Webhook(A_URL)
    assert await drop_webhook(pool, org)
    assert not await drop_webhook(pool, org)
