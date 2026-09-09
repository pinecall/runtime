"""`migrate up` on a real database: the default org comes out of it with one key, once."""

from collections.abc import AsyncIterator
from io import StringIO
from uuid import uuid4

import pytest

from pinecall.auth.keys import PostgresKeys, fingerprint
from pinecall.cli.migrate import issue_the_default_orgs_key
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


async def test_the_first_migrate_issues_the_default_fleets_key_and_the_second_issues_nothing(
    postgres: Dev, fresh: str
) -> None:
    """The promise CLAUDE.md makes, kept: one key, one printing, and never a second one."""
    said = StringIO()
    assert await issue_the_default_orgs_key(postgres.dsn, fresh, said) == 0
    first = said.getvalue()
    key = first.splitlines()[0]
    assert key.startswith("pk_")
    assert first.count(key) == 1, "a key is printed once and nowhere else"

    second = StringIO()
    assert await issue_the_default_orgs_key(postgres.dsn, fresh, second) == 0
    again = second.getvalue()
    assert f"org {DEFAULT_ORG} already has a key" in again
    assert key not in again

    pool = await open_pool(postgres.dsn, schema=fresh)
    try:
        listed = await PostgresKeys(pool).listed(DEFAULT_ORG)
        assert [row.fingerprint for row in listed] == [fingerprint(key)]
        assert await PostgresKeys(pool).verify(key) is not None
    finally:
        await pool.close()


async def test_a_key_revoked_in_the_table_stops_verifying_and_keeps_its_row(
    postgres: Dev, fresh: str
) -> None:
    """Revocation is an UPDATE: the row, and the history that names it, stay."""
    said = StringIO()
    await issue_the_default_orgs_key(postgres.dsn, fresh, said)
    key = said.getvalue().splitlines()[0]
    pool = await open_pool(postgres.dsn, schema=fresh)
    try:
        keys = PostgresKeys(pool)
        assert await keys.revoke(fingerprint(key)) is True
        assert await keys.verify(key) is None
        assert await keys.revoke(fingerprint(key)) is False
        listed = await keys.listed(DEFAULT_ORG)
        assert len(listed) == 1
        assert listed[0].revoked_at is not None
    finally:
        await pool.close()
