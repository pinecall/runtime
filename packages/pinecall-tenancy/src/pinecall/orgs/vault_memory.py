"""The vault in this process's memory: sealed keys kept by org and vendor for as long as it runs."""

from __future__ import annotations

from pinecall.orgs.vault import Cipher, seal, unseal
from pinecall.types import ProviderKeys


class MemoryVault:
    """The vault of a clone with a dev key and no Postgres: the same cipher, forgotten on exit."""

    def __init__(self, cipher: Cipher) -> None:
        self._cipher = cipher
        self._rows: dict[tuple[str, str], str] = {}

    async def put(self, org: str, vendor: str, key: str) -> None:
        """Encrypted here too, so a dev clone and a box behave alike down to the stored bytes."""
        self._rows[(org, vendor)] = seal(self._cipher, key)

    async def drop(self, org: str, vendor: str) -> bool:
        """Whether there was a row to forget."""
        return self._rows.pop((org, vendor), None) is not None

    async def vendors_of(self, org: str) -> tuple[str, ...]:
        """In the order a listing reads them, which is alphabetical."""
        return tuple(sorted(vendor for (kept, vendor) in self._rows if kept == org))

    async def keys_of(self, org: str) -> ProviderKeys:
        """Every key this org brought, decrypted."""
        return {
            vendor: unseal(self._cipher, ciphertext)
            for (kept, vendor), ciphertext in self._rows.items()
            if kept == org
        }
