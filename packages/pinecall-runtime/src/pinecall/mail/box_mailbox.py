"""The box's own mail server: what the operator stored from the console, else the environment's."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from pinecall.orgs.box_settings import MAIL, BoxSettings
from pinecall.orgs.org_mail import KeptMail
from pinecall.settings import Settings
from pinecall.types import DeclarationRefused, Mailbox, parse_mailbox_url, parse_security

logger = logging.getLogger(__name__)

# Where the box's mailbox came from. `stored` wins: what an operator typed into /admin is newer
# than, and deliberately instead of, a line somebody left in an environment file.
type Source = Literal["stored", "environment"]

# A box told to post mail through something that is not a mail URL. Said once, at startup, and
# never again: the doors then behave as they do on a box that was told nothing, which is to send
# no mail and answer exactly as they did before. A refusal to start would be a box that will not
# take a call because a letter cannot be sent.
NOT_A_MAIL_URL = "%s: this box will send no mail — %s"


@dataclass(frozen=True)
class BoxMail:
    """The box's mailbox, its standing, and which of the two places it was read from."""

    kept: KeptMail
    source: Source


class TheBoxsMail:
    """The one answer to "what does this box post a letter through", asked on every letter."""

    def __init__(self, environment: Mailbox | None, box: BoxSettings | None) -> None:
        self._environment = environment
        self._box = box

    async def of(self) -> BoxMail | None:
        """The stored mailbox, else the environment's, else None: this box posts no letters."""
        stored = await self._stored()
        if stored is not None:
            return BoxMail(stored, "stored")
        if self._environment is not None:
            return BoxMail(KeptMail(self._environment), "environment")
        return None

    async def put(self, mailbox: Mailbox) -> None:
        """Keep it, replacing what was stored, standing reset. NoVaultKey with no vault key."""
        if self._box is None:
            return
        value = {
            "host": mailbox.host,
            "port": mailbox.port,
            "security": mailbox.security,
            "username": mailbox.username,
            "sender": mailbox.sender,
            "verified_at": None,
            "last_error": None,
        }
        await self._box.put(MAIL, value, mailbox.password)

    async def drop(self) -> bool:
        """Forget the stored one; the environment's answers again. False when none was stored."""
        return self._box is not None and await self._box.drop(MAIL)

    # Only on the STORED row. The environment's mailbox has nowhere to write to, and its outcome
    # is in the box's log, which is where whoever edits an environment file reads.
    async def recorded(self, error: str | None) -> None:
        """What came of a letter posted through the stored mailbox, beside it."""
        if self._box is None:
            return
        noted: dict[str, str | None] = {"last_error": error}
        if error is None:
            noted["verified_at"] = datetime.now(UTC).isoformat()
        await self._box.noted(MAIL, noted)

    async def _stored(self) -> KeptMail | None:
        """The row as a mailbox with its password, or None: no row, or one no key here opens."""
        kept = None if self._box is None else await self._box.of(MAIL)
        if kept is None or kept.secret is None:
            return None
        said = kept.value
        try:
            mailbox = Mailbox(
                host=str(said["host"]),
                port=int(said["port"]),
                security=parse_security(str(said["security"])),
                username=str(said["username"]),
                password=kept.secret,
                sender=str(said["sender"]),
            )
        except (KeyError, ValueError, DeclarationRefused):
            return None
        return KeptMail(mailbox, said.get("verified_at"), said.get("last_error"))


def environment_mailbox(settings: Settings) -> Mailbox | None:
    """PINECALL_SMTP_URL and PINECALL_MAIL_FROM as one mailbox; None when either is unset."""
    if not settings.smtp_url or not settings.mail_from:
        return None
    try:
        return parse_mailbox_url(settings.smtp_url, settings.mail_from)
    except DeclarationRefused as refused:
        logger.error(NOT_A_MAIL_URL, "PINECALL_SMTP_URL", refused)
        return None
