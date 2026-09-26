"""The ledger of minted call tokens: one row per call, spent once by the dispatch that opens it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from pinecall.db import Pool

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


def tokens_for(pool: Pool | None) -> Tokens:
    """Postgres when the process opened one; memory when it is a clone running on a dev key."""
    # Imported here: both adapters import this module for the port, and the one place that
    # picks between them is the one place the cycle would close (auth/members.py).
    from pinecall.tokens.ledger_memory import MemoryTokens
    from pinecall.tokens.ledger_postgres import PostgresTokens

    return MemoryTokens() if pool is None else PostgresTokens(pool)
