"""The outbound trunks table: one per org, the password it dials with encrypted at rest."""

from __future__ import annotations

import json
from typing import Any, Protocol

from pinecall._settings import Settings
from pinecall.log.store import Pool
from pinecall.orgs.vault import NO_VAULT_KEY, Cipher, NoVaultKey, a_sealed_store, opened, sealed
from pinecall.types import CarrierKind, OutboundTrunk, a_carrier_kind


class OutboundTrunks(Protocol):
    """Where the trunk an org dials through is kept, replaced whole, and read back to dial with."""

    async def put(self, trunk: OutboundTrunk) -> None:
        """Keep the org's outbound trunk, replacing whatever it had. One per org."""
        ...

    async def of(self, org: str) -> OutboundTrunk | None:
        """The org's trunk with its password in the clear, or None when none was provisioned."""
        ...

    async def drop(self, org: str) -> bool:
        """Forget it. False when the org had none."""
        ...


class MemoryOutboundTrunks:
    """The table of a process with no Postgres: the same cipher, forgotten on exit."""

    def __init__(self, cipher: Cipher) -> None:
        self._cipher = cipher
        self._rows: dict[str, tuple[CarrierKind, str, str, str | None, str | None]] = {}

    async def put(self, trunk: OutboundTrunk) -> None:
        """Sealed here too, so a dev clone and a box behave alike down to the stored bytes."""
        self._rows[trunk.org] = (
            trunk.kind,
            trunk.trunk_id,
            trunk.address,
            trunk.username,
            _sealed(self._cipher, trunk.password),
        )

    async def of(self, org: str) -> OutboundTrunk | None:
        """Whether there is one, and what it opens to."""
        row = self._rows.get(org)
        if row is None:
            return None
        kind, trunk_id, address, username, ciphertext = row
        return OutboundTrunk(
            org=org,
            kind=kind,
            trunk_id=trunk_id,
            address=address,
            username=username,
            password=_opened(self._cipher, ciphertext),
        )

    async def drop(self, org: str) -> bool:
        """Whether there was a row to forget."""
        return self._rows.pop(org, None) is not None


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
            _sealed(self._cipher, trunk.password),
        )

    async def of(self, org: str) -> OutboundTrunk | None:
        """One read on the primary key, decrypted for the door that places the call."""
        row = await self._pool.fetchrow(_OF, org)
        if row is None:
            return None
        return OutboundTrunk(
            org=org,
            kind=a_carrier_kind(str(row["kind"])),
            trunk_id=str(row["trunk_id"]),
            address=str(row["address"]),
            username=row["username"],
            password=_opened(self._cipher, row["ciphertext"]),
        )

    async def drop(self, org: str) -> bool:
        """The command tag says whether a row went, so dropping a stranger is told apart."""
        return await self._pool.fetchrow(_DROP, org) is not None


# None when the box was given no vault key, exactly as the carriers table is: the password this
# row holds is one the box MINTED on the tenant's account and cannot read back from anywhere.
def outbound_trunks_for(settings: Settings, pool: Pool | None) -> OutboundTrunks | None:
    """Postgres when the process opened one, memory when it did not, none with no vault key."""
    return a_sealed_store(
        settings, pool, memory=MemoryOutboundTrunks, postgres=PostgresOutboundTrunks
    )


def _sealed(cipher: Cipher, password: str | None) -> str | None:
    """The password as a row keeps it: one Fernet token over its JSON, or nothing at all."""
    if password is None:
        return None
    return sealed(cipher, json.dumps({"password": password}))


def _opened(cipher: Cipher, ciphertext: Any) -> str | None:
    """One column back into the password, or None for a trunk that authenticates with nothing."""
    if not ciphertext:
        return None
    said: dict[str, Any] = json.loads(opened(cipher, str(ciphertext)))
    return str(said["password"])


__all__ = [
    "NO_VAULT_KEY",
    "MemoryOutboundTrunks",
    "NoVaultKey",
    "OutboundTrunks",
    "PostgresOutboundTrunks",
    "outbound_trunks_for",
]
