"""`migrate up` on a real database: the default org, with no key until `keys issue` mints one."""

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest

from pinecall.auth.keys import PostgresKeys, fingerprint
from pinecall.log.store import open_pool
from pinecall.log.store.postgres import apply_migrations
from pinecall.types import DEFAULT_ORG
from tests.cli.conftest import Dev

pytestmark = pytest.mark.postgres


@pytest.fixture
async def fresh(postgres: Dev) -> AsyncIterator[str]:
    """A whole database of this test's own: every migration applied, and not one key in it."""
    schema = f"pinecall_keys_{uuid4().hex[:12]}"
    await apply_migrations(postgres.dsn, schema=schema)
    try:
        yield schema
    finally:
        pool = await open_pool(postgres.dsn)
        await pool.execute(f"drop schema if exists {schema} cascade")
        await pool.close()


# The migration seeds the org and mints nothing: a key exists in the clear at exactly one place,
# the return of `keys issue`, and never in the journal of a unit that runs `migrate up` before
# every start. So a fresh database has an org with no key, until somebody asks for one.
async def test_a_fresh_database_seeds_the_default_org_with_no_key_until_one_is_issued(
    postgres: Dev, fresh: str
) -> None:
    pool = await open_pool(postgres.dsn, schema=fresh)
    try:
        keys = PostgresKeys(pool)
        assert await keys.listed(DEFAULT_ORG) == ()

        issued = await keys.issue(DEFAULT_ORG, "the first")

        assert issued.key.startswith("pk_")
        assert [row.fingerprint for row in await keys.listed(DEFAULT_ORG)] == [
            fingerprint(issued.key)
        ]
        assert await keys.verify(issued.key) is not None
    finally:
        await pool.close()


async def test_a_key_revoked_in_the_table_stops_verifying_and_keeps_its_row(
    postgres: Dev, fresh: str
) -> None:
    """Revocation is an UPDATE: the row, and the history that names it, stay."""
    pool = await open_pool(postgres.dsn, schema=fresh)
    try:
        keys = PostgresKeys(pool)
        key = (await keys.issue(DEFAULT_ORG, "to be revoked")).key
        assert await keys.revoke(fingerprint(key)) is True
        assert await keys.verify(key) is None
        assert await keys.revoke(fingerprint(key)) is False
        listed = await keys.listed(DEFAULT_ORG)
        assert len(listed) == 1
        assert listed[0].revoked_at is not None
    finally:
        await pool.close()
