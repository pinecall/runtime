"""The tokens table on a real Postgres: minted once, spent once, and the second spend refused."""

import time
from collections.abc import AsyncIterator

import pytest

from pinecall.db import open_pool
from pinecall.tokens.ledger import PostgresTokens, TokenRecord
from tests.postgres import Dev

# It lives here and not beside the door it serves, for the reason tests/log/test_routes_table.py
# lives here: the dev stack's fixtures are this package's, and one schema per run is theirs.
pytestmark = pytest.mark.postgres


@pytest.fixture
async def ledger(postgres: Dev) -> AsyncIterator[PostgresTokens]:
    """The table on a pool of this run's own schema. The pool is closed either way."""
    pool = await open_pool(postgres.dsn, schema=postgres.schema)
    try:
        yield PostgresTokens(pool)
    finally:
        await pool.close()


def a_record(call: str, agent: str) -> TokenRecord:
    return TokenRecord(
        call=call,
        org="default",
        agent=agent,
        scope="talk",
        expires_at=time.time() + 60,
    )


async def test_the_guarded_update_spends_a_token_exactly_once(
    ledger: PostgresTokens, call: str, agent: str
) -> None:
    await ledger.minted(a_record(call, agent))
    assert await ledger.spend(call) == "spent"
    assert await ledger.spend(call) == "already_spent"


async def test_a_call_with_no_row_is_never_minted_and_not_already_spent(
    ledger: PostgresTokens, call: str
) -> None:
    assert await ledger.spend(call) == "never_minted"
