"""Each org's mailbox in this process's memory, its password sealed, and its letters' outcomes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime

from pinecall.orgs.org_mail import KeptMail
from pinecall.orgs.vault import Cipher, seal, unseal
from pinecall.types import Mailbox


class MemoryMail:
    """The table of a clone with no Postgres: the same cipher, forgotten when the process exits."""

    def __init__(self, cipher: Cipher, now: Callable[[], str] | None = None) -> None:
        self._cipher = cipher
        self._now = now or _an_instant
        self._rows: dict[str, tuple[KeptMail, str]] = {}

    async def put(self, org: str, mailbox: Mailbox) -> None:
        """Encrypted here too, so a dev clone and a box behave alike down to the stored bytes."""
        kept = KeptMail(replace(mailbox, password=""))
        self._rows[org] = (kept, seal(self._cipher, mailbox.password))

    async def of(self, org: str) -> KeptMail | None:
        """Through the cipher on the way out, exactly as the row below is."""
        row = self._rows.get(org)
        if row is None:
            return None
        kept, ciphertext = row
        in_the_clear = replace(kept.mailbox, password=unseal(self._cipher, ciphertext))
        return replace(kept, mailbox=in_the_clear)

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


def _an_instant() -> str:
    """A moment as Postgres hands its timestamps back: ISO 8601, UTC."""
    return datetime.now(UTC).isoformat()
