"""The org_mail table: one SMTP account per org, its password encrypted at rest."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pinecall.db import Pool
from pinecall.orgs.vault import NO_VAULT_KEY, NoVaultKey, sealed_store
from pinecall.settings import Settings
from pinecall.types import Mailbox


@dataclass(frozen=True)
class KeptMail:
    """What an org wired, and what came of the last letter posted through it."""

    mailbox: Mailbox
    # The last time a letter was taken by this server, and the last sentence it refused one with.
    # One of the two is always null: a send that works clears the error, and one that fails keeps
    # the date of the last that worked, so an admin reads "it worked on Tuesday, and today: …".
    verified_at: str | None = None
    last_error: str | None = None


class Mail(Protocol):
    """Where an org's own mail server is kept, replaced whole, and read back with its password."""

    async def put(self, org: str, mailbox: Mailbox) -> None:
        """Keep this org's mail server, replacing whatever it had. One per org, standing reset."""
        ...

    async def of(self, org: str) -> KeptMail | None:
        """The org's mailbox with its password in the clear, or None when it wired none."""
        ...

    async def drop(self, org: str) -> bool:
        """Forget it. False when the org had none: a typo must not read as done."""
        ...

    async def recorded(self, org: str, error: str | None) -> None:
        """What came of a letter posted through it: the moment it worked, or what was said."""
        ...


# None when the box was given no vault key, exactly as the SSO table and the carriers are: the
# doors that need one answer 503 in the vault's own sentence. An org's mail then goes with it, and
# the box's own mail — which is a credential of the box and not of a tenant — still sends.
def mail_for(settings: Settings, pool: Pool | None) -> Mail | None:
    """Postgres when the process opened one, memory with none, nothing with no vault key."""
    # Imported here: both adapters import this module for the port, and the one place that
    # picks between them is the one place the cycle would close (auth/members.py).
    from pinecall.orgs.org_mail_memory import MemoryMail
    from pinecall.orgs.org_mail_postgres import PostgresMail

    return sealed_store(settings, pool, memory=MemoryMail, postgres=PostgresMail)


__all__ = ["NO_VAULT_KEY", "KeptMail", "Mail", "NoVaultKey", "mail_for"]
