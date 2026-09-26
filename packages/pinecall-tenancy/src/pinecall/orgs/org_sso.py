"""The org_sso table: one identity provider per org, its client secret encrypted at rest."""

from __future__ import annotations

from typing import Protocol

from pinecall.db import Pool
from pinecall.orgs.vault import NO_VAULT_KEY, NoVaultKey, sealed_store
from pinecall.settings import Settings
from pinecall.types import OrgSso


class Sso(Protocol):
    """Where an org's identity provider is kept, replaced whole, and read back with its secret."""

    async def put(self, sso: OrgSso) -> None:
        """Keep this org's provider, replacing whatever it had. One per org."""
        ...

    async def of(self, org: str) -> OrgSso | None:
        """The org's provider with its secret in the clear, or None when it wired none."""
        ...

    async def drop(self, org: str) -> bool:
        """Forget it. False when the org had none: a typo must not read as done."""
        ...

    # What the sign-in page's discovery asks, and the ONE read that spans orgs: which of them
    # sign in with an address of this domain. It answers the configurations whole because the
    # caller needs the org each belongs to; the door hands out the org's name and nothing else.
    async def with_domain(self, domain: str) -> tuple[OrgSso, ...]:
        """Every org that admits this email domain, oldest first."""
        ...


# None when the box was given no vault key, exactly as the carriers are: the doors that need one
# answer 503 in the vault's own sentence. A box that lost its vault key cannot read a client
# secret at all, so SSO stops working there — and a password opens the org again, which is the
# behaviour a locked-out admin wants and the one an operator has to know about.
def sso_for(settings: Settings, pool: Pool | None) -> Sso | None:
    """Postgres when the process opened one, memory with none, nothing with no vault key."""
    # Imported here: both adapters import this module for the port, and the one place that
    # picks between them is the one place the cycle would close (auth/members.py).
    from pinecall.orgs.org_sso_memory import MemorySso
    from pinecall.orgs.org_sso_postgres import PostgresSso

    return sealed_store(settings, pool, memory=MemorySso, postgres=PostgresSso)


__all__ = ["NO_VAULT_KEY", "NoVaultKey", "Sso", "sso_for"]
