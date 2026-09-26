"""Each org's outbound trunk in Postgres, its password sealed under the box's key."""

from __future__ import annotations

from pinecall.db import Pool
from pinecall.orgs.outbound_credentials import opened, sealed
from pinecall.orgs.vault import Cipher
from pinecall.types import OutboundTrunk, parse_carrier_kind

_PUT = """
INSERT INTO outbound_trunks (org, kind, trunk_id, address, username, ciphertext, set_at)
    VALUES ($1, $2, $3, $4, $5, $6, now())
    ON CONFLICT (org) DO UPDATE
    SET kind = excluded.kind, trunk_id = excluded.trunk_id, address = excluded.address,
        username = excluded.username, ciphertext = excluded.ciphertext, set_at = now()
"""
_OF = "SELECT kind, trunk_id, address, username, ciphertext FROM outbound_trunks WHERE org = $1"
_DROP = "DELETE FROM outbound_trunks WHERE org = $1 RETURNING org"


class PostgresOutboundTrunks:
    """The table in Postgres, read on every dial: a trunk repaired now is what the next uses."""

    def __init__(self, pool: Pool, cipher: Cipher) -> None:
        self._pool = pool
        self._cipher = cipher

    async def put(self, trunk: OutboundTrunk) -> None:
        """The password reaches this method in the clear and nothing under it: a token is kept."""
        await self._pool.execute(
            _PUT,
            trunk.org,
            trunk.kind,
            trunk.trunk_id,
            trunk.address,
            trunk.username,
            sealed(self._cipher, trunk.password),
        )

    async def of(self, org: str) -> OutboundTrunk | None:
        """One read on the primary key, decrypted for the door that places the call."""
        row = await self._pool.fetchrow(_OF, org)
        if row is None:
            return None
        return OutboundTrunk(
            org=org,
            kind=parse_carrier_kind(str(row["kind"])),
            trunk_id=str(row["trunk_id"]),
            address=str(row["address"]),
            username=row["username"],
            password=opened(self._cipher, row["ciphertext"]),
        )

    async def drop(self, org: str) -> bool:
        """The command tag says whether a row went, so dropping a stranger is told apart."""
        return await self._pool.fetchrow(_DROP, org) is not None
