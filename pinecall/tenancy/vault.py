"""The vault: the Fernet keys every secret is sealed under, and the vendors' credentials."""

import json
import logging

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from psycopg.types.json import Jsonb

from pinecall.domain.errors import SettingsRefused
from pinecall.domain.types import Credentials, Json
from pinecall.postgres.pool import Pool

logger = logging.getLogger(__name__)

VARIABLE = "PINECALL_VAULT_KEY"
UNSET = (
    f"{VARIABLE} is unset: every vendor key, mailbox and sign-in secret is sealed under it, "
    "so the gateway does not start without it. `Fernet.generate_key()` makes one"
)
NOT_A_KEY = (
    f"{VARIABLE} holds something that is not a Fernet key: a comma-separated list of "
    "32-byte url-safe base64 keys, the newest first"
)

# The box's own credentials sit beside its other settings, one row per vendor.
BOX_CREDENTIALS = "credentials/"

OWN = "SELECT vendor, ciphertext FROM provider_keys WHERE org = %(org)s ORDER BY vendor"
PUT_OWN = """
INSERT INTO provider_keys (org, vendor, ciphertext) VALUES (%(org)s, %(vendor)s, %(ciphertext)s)
ON CONFLICT (org, vendor) DO UPDATE SET ciphertext = excluded.ciphertext, set_at = now()
"""
DROP_OWN = "DELETE FROM provider_keys WHERE org = %(org)s AND vendor = %(vendor)s RETURNING vendor"
BOX = """
SELECT substr(name, length(%(prefix)s) + 1) AS vendor, ciphertext FROM box_settings
WHERE starts_with(name, %(prefix)s) AND ciphertext IS NOT NULL ORDER BY name
"""
PUT_BOX = """
INSERT INTO box_settings (name, value, ciphertext) VALUES (%(name)s, %(value)s, %(ciphertext)s)
ON CONFLICT (name) DO UPDATE SET ciphertext = excluded.ciphertext, set_at = now()
"""
DROP_BOX = "DELETE FROM box_settings WHERE name = %(name)s RETURNING name"


def vault_of(keys: str | None) -> MultiFernet:
    """The vault built from the setting: the first key seals, any key listed opens."""
    if keys is None:
        raise SettingsRefused(UNSET)
    listed = [one.strip() for one in keys.split(",") if one.strip()]
    try:
        return MultiFernet([Fernet(one) for one in listed])
    except ValueError:
        raise SettingsRefused(NOT_A_KEY) from None


def sealed(vault: MultiFernet, secret: Json) -> str:
    """The secret as one Fernet token over its JSON."""
    return vault.encrypt(json.dumps(secret).encode()).decode()


# A token sealed under a key the list no longer holds reads as nothing, said in the log: one
# stale row must not stop every call of the org.
def opened(vault: MultiFernet, token: str) -> Json:
    """The secret a token seals; None when no key listed opens it."""
    try:
        return json.loads(vault.decrypt(token.encode()))
    except InvalidToken:
        logger.warning("a secret sealed under a key %s no longer lists reads as unset", VARIABLE)
        return None


async def credentials_of(pool: Pool, vault: MultiFernet, org: str) -> dict[str, Credentials]:
    """The vendors' credentials the org brought, opened."""
    async with pool.connection() as connection:
        rows = await (await connection.execute(OWN, {"org": org})).fetchall()
    return _opened_rows(vault, [(row["vendor"], row["ciphertext"]) for row in rows])


async def vendors_of(pool: Pool, org: str) -> list[str]:
    """The vendors the org brought its own credentials for, never the credentials."""
    async with pool.connection() as connection:
        rows = await (await connection.execute(OWN, {"org": org})).fetchall()
    return [row["vendor"] for row in rows]


async def put_credentials(
    pool: Pool, vault: MultiFernet, org: str, vendor: str, credentials: Credentials
) -> None:
    """Keep the org's credentials for the vendor, replacing any it had."""
    ciphertext = sealed(vault, credentials)
    async with pool.connection() as connection:
        await connection.execute(PUT_OWN, {"org": org, "vendor": vendor, "ciphertext": ciphertext})


async def drop_credentials(pool: Pool, org: str, vendor: str) -> bool:
    """Forget the org's credentials for the vendor; whether it had any."""
    async with pool.connection() as connection:
        dropped = await connection.execute(DROP_OWN, {"org": org, "vendor": vendor})
        return await dropped.fetchone() is not None


async def box_credentials(pool: Pool, vault: MultiFernet) -> dict[str, Credentials]:
    """The credentials the box holds for the vendors it offers, opened."""
    async with pool.connection() as connection:
        rows = await (await connection.execute(BOX, {"prefix": BOX_CREDENTIALS})).fetchall()
    return _opened_rows(vault, [(row["vendor"], row["ciphertext"]) for row in rows])


async def put_box_credentials(
    pool: Pool, vault: MultiFernet, vendor: str, credentials: Credentials
) -> None:
    """Keep the box's credentials for the vendor: offering it is holding its key."""
    row = {
        "name": f"{BOX_CREDENTIALS}{vendor}",
        "value": Jsonb({}),
        "ciphertext": sealed(vault, credentials),
    }
    async with pool.connection() as connection:
        await connection.execute(PUT_BOX, row)


async def drop_box_credentials(pool: Pool, vendor: str) -> bool:
    """Stop offering the vendor on the box's key; whether the box held one."""
    async with pool.connection() as connection:
        dropped = await connection.execute(DROP_BOX, {"name": f"{BOX_CREDENTIALS}{vendor}"})
        return await dropped.fetchone() is not None


def _opened_rows(vault: MultiFernet, rows: list[tuple[str, str]]) -> dict[str, Credentials]:
    kept: dict[str, Credentials] = {}
    for vendor, ciphertext in rows:
        secret = opened(vault, ciphertext)
        if isinstance(secret, str | dict):
            kept[vendor] = secret
    return kept
