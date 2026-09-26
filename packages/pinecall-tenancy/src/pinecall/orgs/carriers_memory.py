"""Carriers in this process's memory: each org's carrier account, sealed, for as long as it runs."""

from __future__ import annotations

from pinecall.orgs.carriers import opened, sealed
from pinecall.orgs.vault import Cipher
from pinecall.types import Carrier


class MemoryCarriers:
    """The table of a clone with a dev key and no Postgres: the same cipher, forgotten on exit."""

    def __init__(self, cipher: Cipher) -> None:
        self._cipher = cipher
        self._rows: dict[str, tuple[str, str, str]] = {}

    async def put(self, carrier: Carrier) -> None:
        """Encrypted here too, so a dev clone and a box behave alike down to the stored bytes."""
        self._rows[carrier.org] = (carrier.kind, carrier.named, sealed(self._cipher, carrier))

    async def of(self, org: str) -> Carrier | None:
        """Whether there is one, and what it opens to."""
        row = self._rows.get(org)
        return None if row is None else opened(self._cipher, org, row[0], row[2])

    async def drop(self, org: str) -> bool:
        """Whether there was a row to forget."""
        return self._rows.pop(org, None) is not None
