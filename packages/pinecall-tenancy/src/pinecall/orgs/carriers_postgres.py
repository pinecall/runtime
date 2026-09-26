"""Carriers in Postgres: each org's carrier account, its secret sealed under the box's key."""

from __future__ import annotations

from pinecall.db import Pool
from pinecall.orgs.carriers import opened, sealed
from pinecall.orgs.vault import Cipher
from pinecall.types import Carrier

_PUT = """
INSERT INTO carriers (org, kind, account, ciphertext, set_at) VALUES ($1, $2, $3, $4, now())
    ON CONFLICT (org) DO UPDATE
    SET kind = excluded.kind, account = excluded.account, ciphertext = excluded.ciphertext,
        set_at = now()
"""
_OF = "SELECT kind, ciphertext FROM carriers WHERE org = $1"
_DROP = "DELETE FROM carriers WHERE org = $1 RETURNING org"


class PostgresCarriers:
    """The table in Postgres, read on every ask: a carrier set now is what the next import uses."""

    def __init__(self, pool: Pool, cipher: Cipher) -> None:
        self._pool = pool
        self._cipher = cipher

    async def put(self, carrier: Carrier) -> None:
        """The credentials reach this method in the clear and nothing under it: a token is kept."""
        await self._pool.execute(
            _PUT, carrier.org, carrier.kind, carrier.named, sealed(self._cipher, carrier)
        )

    async def of(self, org: str) -> Carrier | None:
        """One read on the primary key, decrypted for the door that acts on the account."""
        row = await self._pool.fetchrow(_OF, org)
        if row is None:
            return None
        return opened(self._cipher, org, str(row["kind"]), str(row["ciphertext"]))

    async def drop(self, org: str) -> bool:
        """The command tag says whether a row went, so dropping a stranger is told apart."""
        return await self._pool.fetchrow(_DROP, org) is not None
