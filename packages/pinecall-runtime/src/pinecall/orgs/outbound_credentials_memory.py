"""Each org's outbound trunk in this process's memory, its password sealed."""

from __future__ import annotations

from pinecall.orgs.outbound_credentials import opened, sealed
from pinecall.orgs.vault import Cipher
from pinecall.types import CarrierKind, OutboundTrunk


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
            sealed(self._cipher, trunk.password),
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
            password=opened(self._cipher, ciphertext),
        )

    async def drop(self, org: str) -> bool:
        """Whether there was a row to forget."""
        return self._rows.pop(org, None) is not None
