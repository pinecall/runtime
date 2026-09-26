"""The vault in Postgres: each org's provider keys, sealed under the box's key, one row a vendor."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pinecall.db import Pool
from pinecall.orgs.vault import Cipher, seal, unseal
from pinecall.types import ProviderKeys

# One row per (org, vendor), replaced whole: a tenant who rotates a key sets it again and the
# previous ciphertext goes with it. There is deliberately no history of a secret in this table.
_PUT = """
INSERT INTO provider_keys (org, vendor, ciphertext, set_at)
    VALUES ($1, $2, $3, now())
    ON CONFLICT (org, vendor) DO UPDATE
    SET ciphertext = excluded.ciphertext, set_at = now()
"""

_DROP = "DELETE FROM provider_keys WHERE org = $1 AND vendor = $2 RETURNING vendor"

_VENDORS = "SELECT vendor FROM provider_keys WHERE org = $1 ORDER BY vendor"

_KEYS = "SELECT vendor, ciphertext FROM provider_keys WHERE org = $1"


class PostgresVault:
    """The table in Postgres, read on every call: a key set now is used by the next call."""

    def __init__(self, pool: Pool, cipher: Cipher) -> None:
        self._pool = pool
        self._cipher = cipher

    async def put(self, org: str, vendor: str, key: str) -> None:
        """The key in the clear reaches this method and nothing under it: the row holds a token."""
        await self._pool.execute(_PUT, org, vendor, seal(self._cipher, key))

    async def drop(self, org: str, vendor: str) -> bool:
        """The row RETURNING says whether one went, so dropping a stranger is told apart."""
        return await self._pool.fetchrow(_DROP, org, vendor) is not None

    async def vendors_of(self, org: str) -> tuple[str, ...]:
        """Names only. This is what an operator's listing is built from."""
        return tuple(str(row["vendor"]) for row in await self._pool.fetch(_VENDORS, org))

    async def keys_of(self, org: str) -> ProviderKeys:
        """Every key this org brought, decrypted for the one door that may carry them."""
        rows: Sequence[Mapping[str, Any]] = await self._pool.fetch(_KEYS, org)
        return {str(row["vendor"]): unseal(self._cipher, str(row["ciphertext"])) for row in rows}
