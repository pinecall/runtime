"""The room tokens this process minted, in its own memory: spent once, then refused."""

from __future__ import annotations

import time
from dataclasses import replace

from pinecall.tokens.ledger import Spending, TokenRecord


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
