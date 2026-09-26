"""The ledger of minted call tokens: one row per call, spent once by the dispatch that opens it."""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import Literal, Protocol

from pinecall.log.store import Pool

# What spending a call's token comes back as. `spent` is the one yes; the two refusals are told
# apart because the sentence a worker reads should say which: a token used twice, or a call this
# runtime has no record of minting a token for.
type Spending = Literal["spent", "already_spent", "never_minted"]


@dataclass(frozen=True)
class TokenRecord:
    """One minted token as the ledger keeps it: the call it opens, whose agent, until when."""

    call: str
    org: str
    agent: str
    scope: str
    expires_at: float
    spent_at: float | None = None


class Tokens(Protocol):
    """Where the door records a token it minted, and where the dispatch spends it."""

    async def minted(self, record: TokenRecord) -> None:
        """Remember the token, unspent. The call it names does not exist yet."""
        ...

    async def spend(self, call: str) -> Spending:
        """Mark the call's token spent, once. The second time, and an unknown call, are refusals."""
        ...


class MemoryTokens:
    """The ledger of a process with no database: a dev clone spends, and forgets when it exits."""

    def __init__(self) -> None:
        self._rows: dict[str, TokenRecord] = {}

    async def minted(self, record: TokenRecord) -> None:
        """One row per call, as the table's primary key would insist."""
        self._rows[record.call] = record

    async def spend(self, call: str) -> Spending:
        """The same three answers Postgres gives, from a dict."""
        row = self._rows.get(call)
        if row is None:
            return "never_minted"
        if row.spent_at is not None:
            return "already_spent"
        self._rows[call] = replace(row, spent_at=time.time())
        return "spent"


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


def tokens_for(pool: Pool | None) -> Tokens:
    """Postgres when the process opened one; memory when it is a clone running on a dev key."""
    return MemoryTokens() if pool is None else PostgresTokens(pool)
