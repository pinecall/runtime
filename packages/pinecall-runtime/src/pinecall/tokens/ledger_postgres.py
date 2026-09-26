"""The room tokens minted, in Postgres: spent once across every gateway, then refused."""

from __future__ import annotations

from pinecall.db import Pool
from pinecall.tokens.ledger import Spending, TokenRecord

_MINTED = """
INSERT INTO tokens (call, org, agent, scope, expires_at)
VALUES ($1, $2, $3, $4, to_timestamp($5))
"""

# Spent is an UPDATE guarded by its own WHERE: two dispatches racing for one token reach this row
# in some order, and exactly one of them changes it. The command tag says which one this was.
_SPEND = """
UPDATE tokens SET spent_at = now() WHERE call = $1 AND spent_at IS NULL RETURNING call
"""

_KNOWN = """
SELECT 1 FROM tokens WHERE call = $1
"""


class PostgresTokens:
    """The ledger in Postgres, shared by every gateway of the box: a token spent here is spent."""

    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def minted(self, record: TokenRecord) -> None:
        """One row, keyed by the call. The expiry is kept so an operator can read what was live."""
        await self._pool.execute(
            _MINTED,
            record.call,
            record.org,
            record.agent,
            record.scope,
            record.expires_at,
        )

    async def spend(self, call: str) -> Spending:
        """One guarded UPDATE; only a refusal asks a second question, to name which refusal."""
        if await self._pool.fetchrow(_SPEND, call) is not None:
            return "spent"
        known = await self._pool.fetchrow(_KNOWN, call)
        return "never_minted" if known is None else "already_spent"
