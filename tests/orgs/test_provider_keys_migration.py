"""0007 on a box that already has orgs: the table lands, and a tenant's rows go with its org."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, cast
from uuid import uuid4

import asyncpg  # type: ignore[import-untyped]  # pyright: ignore[reportMissingTypeStubs]
import pytest

from pinecall.log.store.postgres import (
    MIGRATIONS,
    MIGRATIONS_TABLE,
    RECORD_MIGRATION,
    apply_migrations,
)
from tests.postgres import Dev

pytestmark = pytest.mark.postgres

# The schema as it stood the day before provider keys: every migration up to and including orgs.
BEFORE_PROVIDER_KEYS = tuple(
    path.name for path in sorted(MIGRATIONS.glob("*.sql")) if path.name < "0007"
)

THE_ORG = "clinica"
A_CIPHERTEXT = "gAAAAABn-a-fernet-token-and-never-a-key"

_connect = cast("Any", asyncpg.connect)  # pyright: ignore[reportUnknownMemberType]


@dataclass(frozen=True)
class Box:
    """A database as a box had it before 0007, and the connection this test reads it through."""

    dsn: str
    schema: str
    connection: Any


@pytest.fixture
async def a_box_from_before(postgres: Dev) -> AsyncIterator[Box]:
    """Every migration up to orgs applied by hand, and one tenant already created."""
    schema = f"pinecall_before_provider_keys_{uuid4().hex[:12]}"
    connection = await _connect(postgres.dsn)
    try:
        await connection.execute(f"create schema {schema}")
        await connection.execute(f"set search_path to {schema}")
        await connection.execute(MIGRATIONS_TABLE)
        for name in BEFORE_PROVIDER_KEYS:
            await connection.execute((MIGRATIONS / name).read_text(encoding="utf-8"))
            await connection.execute(RECORD_MIGRATION, name)
        await connection.execute("insert into orgs (id, slug, name) values ($1, $1, $1)", THE_ORG)
        yield Box(dsn=postgres.dsn, schema=schema, connection=connection)
    finally:
        await connection.execute(f"drop schema if exists {schema} cascade")
        await connection.close()


async def test_0007_applies_on_top_of_0006_and_leaves_one_row_per_org_and_vendor(
    a_box_from_before: Box,
) -> None:
    """Criterion 4: a box that is up takes the migration with no manual step and no downtime."""
    box = a_box_from_before
    assert (await apply_migrations(box.dsn, schema=box.schema))[0] == "0007_provider_keys.sql"
    read = box.connection
    await read.execute(
        "insert into provider_keys (org, vendor, ciphertext) values ($1, $2, $3)",
        THE_ORG,
        "elevenlabs",
        A_CIPHERTEXT,
    )
    row = await read.fetchrow("select org, vendor, ciphertext, set_at from provider_keys")
    assert (row["org"], row["vendor"], row["ciphertext"]) == (THE_ORG, "elevenlabs", A_CIPHERTEXT)
    assert row["set_at"] is not None


async def test_one_key_per_org_and_vendor_and_a_second_one_replaces_it(
    a_box_from_before: Box,
) -> None:
    """The primary key is the whole of rotation: a tenant sets it again and the old token goes."""
    box = a_box_from_before
    await apply_migrations(box.dsn, schema=box.schema)
    upsert = (
        "insert into provider_keys (org, vendor, ciphertext) values ($1, $2, $3)"
        " on conflict (org, vendor) do update set ciphertext = excluded.ciphertext"
    )
    await box.connection.execute(upsert, THE_ORG, "soniox", A_CIPHERTEXT)
    await box.connection.execute(upsert, THE_ORG, "soniox", "gAAAAAB-the-next-one")
    rows = await box.connection.fetch(
        "select ciphertext from provider_keys where org = $1", THE_ORG
    )
    assert [row["ciphertext"] for row in rows] == ["gAAAAAB-the-next-one"]


async def test_removing_the_org_takes_its_keys_with_it(a_box_from_before: Box) -> None:
    """The foreign key cascades, so `orgs rm` never leaves a tenant's secret behind in the table."""
    box = a_box_from_before
    await apply_migrations(box.dsn, schema=box.schema)
    await box.connection.execute(
        "insert into provider_keys (org, vendor, ciphertext) values ($1, $2, $3)",
        THE_ORG,
        "openai",
        A_CIPHERTEXT,
    )
    await box.connection.execute("delete from orgs where id = $1", THE_ORG)
    assert await box.connection.fetchval("select count(*) from provider_keys") == 0
