"""The vault: one key list seals every secret, and a vendor's credentials are kept only sealed."""

import logging

import pytest
from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from psycopg import sql

from pinecall.domain.names import JsonObject
from pinecall.domain.telemetry import Telemetry
from pinecall.fleet.worlds import FLEETS, Fleets, set_fleets
from pinecall.log import private
from pinecall.log.store import Store
from pinecall.postgres.pool import Pool
from pinecall.process.connections import keyring_of, vault_of
from pinecall.tenancy import carriers, hosting, mail, org_secrets, recording_keys, sso, telemetry
from pinecall.tenancy.carriers import SipPeer
from pinecall.tenancy.orgs import create, remove
from pinecall.tenancy.signin import SignIns
from pinecall.tenancy.vault import (
    SEALED_COLUMNS,
    box_credentials,
    credentials_of,
    drop_box_credentials,
    drop_credentials,
    opened,
    put_box_credentials,
    put_credentials,
    resealed,
    sealed,
    vendors_of,
)
from pinecall.tenancy.words import Words
from tests.conftest import postgres

OLD = Fernet.generate_key().decode()
NEW = Fernet.generate_key().decode()
AZURE: JsonObject = {"speech_key": "made-up-by-this-test", "speech_region": "westeurope"}

# The version byte a Fernet token starts with, base64url: how a sealed value looks in any column.
FERNET = "gAAAAA"

# A column whose name says it holds a secret sealed, and whose type can.
SEALED_NAMES = """
SELECT table_name AS table, column_name AS column FROM information_schema.columns
WHERE table_schema = %(schema)s AND data_type IN ('text', 'character varying')
  AND (column_name = 'ciphertext' OR column_name LIKE 'sealed%%')
"""

EVERY_TEXT = """
SELECT table_name AS table, column_name AS column FROM information_schema.columns
WHERE table_schema = %(schema)s
  AND data_type IN ('text', 'character varying', 'jsonb', 'json', 'ARRAY')
"""

SHAPED_LIKE_ONE = "SELECT {column}::text AS value FROM {table} WHERE {column}::text LIKE %(shape)s"


async def everything_sealed(pool: Pool, vault: MultiFernet) -> None:
    """One secret through every writer that seals: every sealed column holds a row."""
    org = await create(pool, "clinica-norte", "Clínica Norte")
    await put_credentials(pool, vault, org.id, "azure", AZURE)
    await telemetry.put_telemetry(
        pool, vault, org.id, Telemetry("https://otel.test/v1/traces", {"x-api-key": "made-up"})
    )
    await put_box_credentials(pool, vault, "cartesia", "box-made-up")
    peer = SipPeer.model_validate({"username": "pbx", "password": "p", "addresses": ["10.0.0.0/8"]})
    await carriers.put_carrier(pool, vault, org.id, peer)
    box = mail.Mailbox("smtp.box.test", 465, "tls", "box", "box-pass", "Box <no-reply@box.test>")
    await mail.put_box_mail(pool, vault, box)
    theirs = mail.Mailbox("smtp.clinica.test", 587, "starttls", "c", "their-pass", "C <c@c.test>")
    await mail.put_mail(pool, vault, org.id, theirs)
    client = sso.Client("https://idp.test", "the-client", "shh-made-up")
    await sso.put_sso(pool, vault, sso.OrgSso(org.id, client, ("clinica.test",)))
    app = hosting.HostedApp(org=org.id, env="production", name="support")
    await hosting.open_app(pool, vault, app, created_by="m_ana")
    secret = org_secrets.Secret(env="production", name="CRM_TOKEN", value="made-up")
    await org_secrets.put_secret(pool, vault, org.id, secret, set_by="m_ana")
    phone: JsonObject = {"arguments": {"phone": "+34600111222"}}
    await private.kept_aside(Store(pool), vault, "CA_private", [(3, phone)])
    await recording_keys.key_for(pool, vault, org.id, "CA_recorded")
    await SignIns.kept(Words(pool, vault)).pairings.open("Ana's laptop")


async def tokens_by_column(pool: Pool, schema: str) -> dict[str, list[str]]:
    """Every Fernet-shaped value in any text or JSON column of the schema, by table.column."""
    found: dict[str, list[str]] = {}
    async with pool.connection() as connection:
        columns = await (await connection.execute(EVERY_TEXT, {"schema": schema})).fetchall()
        for column in columns:
            query = sql.SQL(SHAPED_LIKE_ONE).format(
                table=sql.Identifier(column["table"]), column=sql.Identifier(column["column"])
            )
            rows = await (await connection.execute(query, {"shape": f"%{FERNET}%"})).fetchall()
            if rows:
                found[f"{column['table']}.{column['column']}"] = [row["value"] for row in rows]
    return found


def test_a_secret_sealed_under_the_old_key_opens_once_the_new_one_is_in_front() -> None:
    token = sealed(vault_of(OLD), AZURE)
    assert opened(vault_of(f"{NEW}, {OLD}"), token) == AZURE


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


def test_a_secret_sealed_under_a_key_no_longer_listed_reads_as_unset_and_says_so(
    caplog: pytest.LogCaptureFixture,
) -> None:
    token = sealed(vault_of(OLD), "a-secret")
    with caplog.at_level(logging.WARNING):
        assert opened(vault_of(NEW), token) is None
    assert "PINECALL_VAULT_KEY" in caplog.text


@postgres
async def test_the_schema_seals_in_no_column_the_rotation_does_not_walk(
    pool: Pool, schema: str
) -> None:
    await everything_sealed(pool, vault_of(OLD))
    walked = {column.named for column in SEALED_COLUMNS}
    async with pool.connection() as connection:
        named = await (await connection.execute(SEALED_NAMES, {"schema": schema})).fetchall()
    assert {f"{row['table']}.{row['column']}" for row in named} == walked
    assert set(await tokens_by_column(pool, schema)) == walked


@postgres
async def test_every_sealed_value_is_resealed_under_the_first_key_once_and_a_rerun_does_nothing(
    pool: Pool, schema: str
) -> None:
    await everything_sealed(pool, vault_of(OLD))
    lost = Fernet.generate_key().decode()
    await put_credentials(pool, vault_of(lost), "default", "deepgram", "sealed-by-nobody-listed")
    keyring = keyring_of(f"{NEW},{OLD}")
    first = {item.column.named: item for item in await resealed(pool, keyring)}
    again = {item.column.named: item for item in await resealed(pool, keyring)}
    assert all(item.resealed >= 1 and item.current == 0 for item in first.values())
    assert first["provider_keys.ciphertext"].unopened == 1
    assert all(item.resealed == 0 for item in again.values())
    assert {name: item.current for name, item in again.items()} == {
        name: item.resealed for name, item in first.items()
    }
    only_the_new = Fernet(NEW)
    for name, values in (await tokens_by_column(pool, schema)).items():
        opened_by_new = [value for value in values if _opens(only_the_new, value)]
        expected = len(values) - (1 if name == "provider_keys.ciphertext" else 0)
        assert len(opened_by_new) == expected, name


def _opens(key: Fernet, token: str) -> bool:
    try:
        key.decrypt(token.encode())
    except InvalidToken:
        return False
    return True
