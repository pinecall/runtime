"""The vault: the Fernet keys every secret is sealed under, and the vendors' credentials."""

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from psycopg import sql
from psycopg.types.json import Jsonb

from pinecall.domain.names import Credentials, Json
from pinecall.postgres.pool import Pool

logger = logging.getLogger(__name__)

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


# A row's ctid names it for one statement, and the value it still holds guards the write: a row
# the gateway wrote meanwhile was written under the first key already, and is left as it is.
TOKENS = "SELECT ctid::text AS row, {column} AS token FROM {table} WHERE {column} IS NOT NULL"
RESEAL = "UPDATE {table} SET {column} = %(new)s WHERE ctid = %(row)s::tid AND {column} = %(old)s"


@dataclass(frozen=True)
class SealedColumn:
    """A column a secret is kept sealed in, by its table."""

    table: str
    column: str

    @property
    def named(self) -> str:
        """The column as a person reads it: table.column."""
        return f"{self.table}.{self.column}"


@dataclass(frozen=True)
class Resealing:
    """One column after a pass: re-sealed now, already under the first key, opened by no key."""

    column: SealedColumn
    resealed: int = 0
    current: int = 0
    unopened: int = 0


# Every column of the schema a secret is sealed in. A test walks the schema and every Fernet token
# in it, so a column added without its line here fails the suite.
SEALED_COLUMNS: tuple[SealedColumn, ...] = (
    SealedColumn("box_settings", "ciphertext"),
    SealedColumn("call_private", "sealed"),
    SealedColumn("carriers", "ciphertext"),
    SealedColumn("hosted_apps", "sealed_key"),
    SealedColumn("one_use_words", "sealed"),
    SealedColumn("org_mail", "ciphertext"),
    SealedColumn("org_secrets", "sealed"),
    SealedColumn("org_sso", "ciphertext"),
    SealedColumn("org_telemetry", "ciphertext"),
    SealedColumn("provider_keys", "ciphertext"),
    SealedColumn("recording_keys", "sealed"),
)


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
        logger.warning(
            "a secret sealed under a key PINECALL_VAULT_KEY no longer lists reads as unset"
        )
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


# Row by row, each its own statement: a pass cut short keeps what it did, and the next pass
# finds those rows under the first key and leaves them. The first key must be first everywhere
# (the gateway restarted on the new list) before the old one is taken out.
async def resealed(pool: Pool, keyring: Sequence[Fernet]) -> list[Resealing]:
    """Seal every sealed value of the schema under the keyring's first key, and count each."""
    return [await _resealed_column(pool, keyring, column) for column in SEALED_COLUMNS]


async def _resealed_column(
    pool: Pool, keyring: Sequence[Fernet], column: SealedColumn
) -> Resealing:
    names = {"table": sql.Identifier(column.table), "column": sql.Identifier(column.column)}
    ring, first = MultiFernet(keyring), keyring[0]
    async with pool.connection() as connection:
        rows = await (await connection.execute(sql.SQL(TOKENS).format(**names))).fetchall()
    resealed = current = unopened = 0
    for row in rows:
        token = str(row["token"]).encode()
        if _opens(first, token):
            current += 1
            continue
        try:
            new = ring.rotate(token).decode()
        except InvalidToken:
            unopened += 1
            continue
        values = {"new": new, "row": row["row"], "old": row["token"]}
        async with pool.connection() as connection:
            written = await connection.execute(sql.SQL(RESEAL).format(**names), values)
        resealed += written.rowcount
    return Resealing(column, resealed=resealed, current=current, unopened=unopened)


def _opens(key: Fernet, token: bytes) -> bool:
    try:
        key.decrypt(token)
    except InvalidToken:
        return False
    return True


def _opened_rows(vault: MultiFernet, rows: list[tuple[str, str]]) -> dict[str, Credentials]:
    kept: dict[str, Credentials] = {}
    for vendor, ciphertext in rows:
        secret = opened(vault, ciphertext)
        if isinstance(secret, str | dict):
            kept[vendor] = secret
    return kept
