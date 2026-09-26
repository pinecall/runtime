"""The outbound trunks table: one per org, the password it dials with encrypted at rest."""

from __future__ import annotations

import json
from typing import Any, Protocol

from pinecall.db import Pool
from pinecall.orgs.vault import NO_VAULT_KEY, Cipher, NoVaultKey, seal, sealed_store, unseal
from pinecall.settings import Settings
from pinecall.types import OutboundTrunk


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


# None when the box was given no vault key, exactly as the carriers table is: the password this
# row holds is one the box MINTED on the tenant's account and cannot read back from anywhere.
def outbound_trunks_for(settings: Settings, pool: Pool | None) -> OutboundTrunks | None:
    """Postgres when the process opened one, memory when it did not, none with no vault key."""
    # Imported here: both adapters import this module for the port, and the one place that
    # picks between them is the one place the cycle would close (auth/members.py).
    from pinecall.orgs.outbound_credentials_memory import MemoryOutboundTrunks
    from pinecall.orgs.outbound_credentials_postgres import PostgresOutboundTrunks

    return sealed_store(
        settings, pool, memory=MemoryOutboundTrunks, postgres=PostgresOutboundTrunks
    )


def sealed(cipher: Cipher, password: str | None) -> str | None:
    """The password as a row keeps it: one Fernet token over its JSON, or nothing at all."""
    if password is None:
        return None
    return seal(cipher, json.dumps({"password": password}))


def opened(cipher: Cipher, ciphertext: Any) -> str | None:
    """One column back into the password, or None for a trunk that authenticates with nothing."""
    if not ciphertext:
        return None
    said: dict[str, Any] = json.loads(unseal(cipher, str(ciphertext)))
    return str(said["password"])


__all__ = ["NO_VAULT_KEY", "NoVaultKey", "OutboundTrunks", "outbound_trunks_for"]
