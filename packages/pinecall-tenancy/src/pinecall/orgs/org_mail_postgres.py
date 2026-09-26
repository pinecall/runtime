"""Each org's mailbox in Postgres, its password sealed, and what came of its last letter."""

from __future__ import annotations

from typing import Any

from pinecall.db import Pool
from pinecall.orgs.org_mail import KeptMail
from pinecall.orgs.vault import Cipher, seal, unseal
from pinecall.types import Mailbox, parse_security

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

_DROP = "DELETE FROM org_mail WHERE org = $1 RETURNING org"

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
            seal(self._cipher, mailbox.password),
            mailbox.sender,
        )

    async def of(self, org: str) -> KeptMail | None:
        """One read on the primary key, decrypted for the one place that opens a socket."""
        row = await self._pool.fetchrow(_OF, org)
        return None if row is None else _a_mailbox(self._cipher, row)

    async def drop(self, org: str) -> bool:
        """The command tag says whether a row went, so dropping a stranger is told apart."""
        return await self._pool.fetchrow(_DROP, org) is not None

    async def recorded(self, org: str, error: str | None) -> None:
        """One UPDATE either way: an org that wired nothing has no row and nothing is written."""
        await self._pool.execute(_RECORDED, org, error)


def _a_mailbox(cipher: Cipher, row: Any) -> KeptMail:
    """One row back into the domain's own Mailbox, the SMTP password in the clear."""
    verified = row["verified_at"]
    return KeptMail(
        mailbox=Mailbox(
            host=str(row["host"]),
            port=int(row["port"]),
            security=parse_security(str(row["security"])),
            username=str(row["username"]),
            password=unseal(cipher, str(row["ciphertext"])),
            sender=str(row["sender"]),
        ),
        verified_at=None if verified is None else verified.isoformat(),
        last_error=None if row["last_error"] is None else str(row["last_error"]),
    )
