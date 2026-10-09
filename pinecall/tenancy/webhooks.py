"""An org's webhook row: the URL in clear, the secret sealed in the vault, read at every alert."""

from cryptography.fernet import MultiFernet

from pinecall.domain.webhook import Webhook
from pinecall.postgres.pool import Pool
from pinecall.tenancy.vault import opened, sealed

ONE = "SELECT url, ciphertext FROM org_webhooks WHERE org = %(org)s"
PUT = """
INSERT INTO org_webhooks (org, url, ciphertext)
VALUES (%(org)s, %(url)s, %(ciphertext)s)
ON CONFLICT (org) DO UPDATE SET
    url = excluded.url, ciphertext = excluded.ciphertext, set_at = now()
"""
DROP = "DELETE FROM org_webhooks WHERE org = %(org)s RETURNING org"


async def put_webhook(pool: Pool, vault: MultiFernet, org: str, webhook: Webhook) -> None:
    """Keep where the org's alerts go, replacing what it had."""
    ciphertext = None if webhook.secret is None else sealed(vault, webhook.secret)
    values = {"org": org, "url": webhook.url, "ciphertext": ciphertext}
    async with pool.connection() as connection:
        await connection.execute(PUT, values)


async def webhook_of(pool: Pool, vault: MultiFernet, org: str) -> Webhook | None:
    """Where the org's alerts go, secret opened; None when they go nowhere."""
    async with pool.connection() as connection:
        row = await (await connection.execute(ONE, {"org": org})).fetchone()
    if row is None:
        return None
    secret = opened(vault, str(row["ciphertext"])) if row["ciphertext"] else None
    return Webhook(url=str(row["url"]), secret=None if secret is None else str(secret))


async def drop_webhook(pool: Pool, org: str) -> bool:
    """Forget where the org's alerts went; whether they went anywhere."""
    async with pool.connection() as connection:
        dropped = await connection.execute(DROP, {"org": org})
        return await dropped.fetchone() is not None
