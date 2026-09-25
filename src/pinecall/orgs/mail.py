"""The org_mail table: one SMTP account per org, its password encrypted at rest."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Protocol

from pinecall._settings import Settings
from pinecall.log.store import Pool
from pinecall.orgs.table import DELETED_NOTHING
from pinecall.orgs.vault import NO_VAULT_KEY, Cipher, NoVaultKey, a_cipher
from pinecall.types import Mailbox, a_security


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


class MemoryMail:
    """The table of a clone with no Postgres: the same cipher, forgotten when the process exits."""

    def __init__(self, cipher: Cipher, now: Callable[[], str] | None = None) -> None:
        self._cipher = cipher
        self._now = now or _an_instant
        self._rows: dict[str, tuple[KeptMail, str]] = {}

    async def put(self, org: str, mailbox: Mailbox) -> None:
        """Encrypted here too, so a dev clone and a box behave alike down to the stored bytes."""
        kept = KeptMail(replace(mailbox, password=""))
        self._rows[org] = (kept, _sealed(self._cipher, mailbox.password))

    async def of(self, org: str) -> KeptMail | None:
        """Through the cipher on the way out, exactly as the row below is."""
        row = self._rows.get(org)
        if row is None:
            return None
        kept, ciphertext = row
        opened = replace(kept.mailbox, password=_opened(self._cipher, ciphertext))
        return replace(kept, mailbox=opened)

    async def drop(self, org: str) -> bool:
        """Whether there was a row to forget."""
        return self._rows.pop(org, None) is not None

    async def recorded(self, org: str, error: str | None) -> None:
        """The same two columns as the row below, written in the same breath."""
        row = self._rows.get(org)
        if row is None:
            return
        kept, ciphertext = row
        moment = self._now() if error is None else kept.verified_at
        self._rows[org] = (replace(kept, verified_at=moment, last_error=error), ciphertext)


# One row per org, replaced whole: an admin who rotates the SMTP password sets the whole mailbox
# again, and the standing goes with it — what a server said about the old password is not news
# about the new one. There is deliberately no history of a secret in this table.
_PUT = """
INSERT INTO org_mail (org, host, port, security, username, ciphertext, sender, set_at)
    VALUES ($1, $2, $3, $4, $5, $6, $7, now())
    ON CONFLICT (org) DO UPDATE
    SET host = excluded.host, port = excluded.port, security = excluded.security,
        username = excluded.username, ciphertext = excluded.ciphertext,
        sender = excluded.sender, verified_at = NULL, last_error = NULL, set_at = now()
"""

_OF = """
SELECT host, port, security, username, ciphertext, sender, verified_at, last_error
  FROM org_mail WHERE org = $1
"""

_DROP = "DELETE FROM org_mail WHERE org = $1"

_RECORDED = """
UPDATE org_mail
   SET verified_at = CASE WHEN $2::text IS NULL THEN now() ELSE verified_at END,
       last_error = $2
 WHERE org = $1
"""


class PostgresMail:
    """The table in Postgres, read on every letter: what is set now is what the next one posts."""

    def __init__(self, pool: Pool, cipher: Cipher) -> None:
        self._pool = pool
        self._cipher = cipher

    async def put(self, org: str, mailbox: Mailbox) -> None:
        """The password reaches this method in the clear and nothing under it: a token is kept."""
        await self._pool.execute(
            _PUT,
            org,
            mailbox.host,
            mailbox.port,
            mailbox.security,
            mailbox.username,
            _sealed(self._cipher, mailbox.password),
            mailbox.sender,
        )

    async def of(self, org: str) -> KeptMail | None:
        """One read on the primary key, decrypted for the one place that opens a socket."""
        row = await self._pool.fetchrow(_OF, org)
        return None if row is None else _a_mailbox(self._cipher, row)

    async def drop(self, org: str) -> bool:
        """The command tag says whether a row went, so dropping a stranger is told apart."""
        tag = await self._pool.execute(_DROP, org)
        return tag.strip() != DELETED_NOTHING

    async def recorded(self, org: str, error: str | None) -> None:
        """One UPDATE either way: an org that wired nothing has no row and nothing is written."""
        await self._pool.execute(_RECORDED, org, error)


# None when the box was given no vault key, exactly as the SSO table and the carriers are: the
# doors that need one answer 503 in the vault's own sentence. An org's mail then goes with it, and
# the box's own mail — which is a credential of the box and not of a tenant — still sends.
def mail_for(settings: Settings, pool: Pool | None) -> Mail | None:
    """Postgres when the process opened one, memory with none, nothing with no vault key."""
    if not settings.vault_key:
        return None
    cipher = a_cipher(settings.vault_key)
    return MemoryMail(cipher) if pool is None else PostgresMail(pool, cipher)


def _a_mailbox(cipher: Cipher, row: Any) -> KeptMail:
    """One row back into the domain's own Mailbox, the SMTP password in the clear."""
    verified = row["verified_at"]
    return KeptMail(
        mailbox=Mailbox(
            host=str(row["host"]),
            port=int(row["port"]),
            security=a_security(str(row["security"])),
            username=str(row["username"]),
            password=_opened(cipher, str(row["ciphertext"])),
            sender=str(row["sender"]),
        ),
        verified_at=None if verified is None else verified.isoformat(),
        last_error=None if row["last_error"] is None else str(row["last_error"]),
    )


def _sealed(cipher: Cipher, secret: str) -> str:
    """The SMTP password as a row keeps it: a Fernet token, never the password itself."""
    return cipher.encrypt(secret.encode()).decode()


def _opened(cipher: Cipher, ciphertext: str) -> str:
    """One row back into the password the mail server takes."""
    return cipher.decrypt(ciphertext.encode()).decode()


def _an_instant() -> str:
    """A moment as Postgres hands its timestamps back: ISO 8601, UTC."""
    return datetime.now(UTC).isoformat()


__all__ = [
    "NO_VAULT_KEY",
    "KeptMail",
    "Mail",
    "MemoryMail",
    "NoVaultKey",
    "PostgresMail",
    "mail_for",
]
