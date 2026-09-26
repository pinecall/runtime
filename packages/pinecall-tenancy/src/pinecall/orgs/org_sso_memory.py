"""Each org's OpenID provider in this process's memory, its client secret sealed."""

from __future__ import annotations

from pinecall.orgs.vault import Cipher, seal, unseal
from pinecall.types import OrgSso


class MemorySso:
    """The table of a clone with no Postgres: the same cipher, forgotten when the process exits."""

    def __init__(self, cipher: Cipher) -> None:
        self._cipher = cipher
        self._rows: dict[str, tuple[OrgSso, str]] = {}

    async def put(self, sso: OrgSso) -> None:
        """Encrypted here too, so a dev clone and a box behave alike down to the stored bytes."""
        self._rows[sso.org] = (sso, seal(self._cipher, sso.client_secret))

    async def of(self, org: str) -> OrgSso | None:
        """Through the cipher on the way out, exactly as the row below is."""
        row = self._rows.get(org)
        if row is None:
            return None
        kept, ciphertext = row
        return OrgSso(
            org=kept.org,
            issuer=kept.issuer,
            client_id=kept.client_id,
            client_secret=unseal(self._cipher, ciphertext),
            domains=kept.domains,
            role=kept.role,
            required=kept.required,
        )

    async def drop(self, org: str) -> bool:
        """Whether there was a row to forget."""
        return self._rows.pop(org, None) is not None

    async def with_domain(self, domain: str) -> tuple[OrgSso, ...]:
        """In insertion order, which for a dict is the order they were wired."""
        found = [org for org, (kept, _) in self._rows.items() if domain in kept.domains]
        opened = [await self.of(org) for org in found]
        return tuple(one for one in opened if one is not None)
