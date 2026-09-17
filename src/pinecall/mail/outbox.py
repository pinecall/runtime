"""Where a door hands a letter: the org's own mail when it wired one, else the box's, or nobody."""

from __future__ import annotations

import asyncio
import logging

from pinecall._settings import Settings
from pinecall.mail.letters import Letter
from pinecall.mail.smtp import MailRefused, posted
from pinecall.orgs.mail import Mail
from pinecall.types import DeclarationRefused, Mailbox, a_mailbox_at

logger = logging.getLogger(__name__)

# A box told to post mail through something that is not a mail URL. Said once, at startup, and
# never again: the doors then behave as they do on a box that was told nothing, which is to send
# no mail and answer exactly as they did before. A refusal to start would be a box that will not
# take a call because a letter cannot be sent.
NOT_A_MAIL_URL = "%s: this box will send no mail — %s"

# What the process log says about a letter. The org's own mail records its outcome on its own row,
# where the admin who wired it reads it (GET /v1/org/mail); the BOX's mail is the operator's, and
# the operator reads the box's log.
POSTED = "mailed %s to %s through %s"
NOT_POSTED = "could not mail %s to %s — %s"


class Outbox:
    """The one place a letter leaves by. It never raises: a door has already answered."""

    def __init__(self, box: Mailbox | None, mail: Mail | None) -> None:
        self._box = box
        self._mail = mail
        # Held, because a task nobody holds is a task the loop may collect mid-send. They are
        # discarded as they finish, so this is the letters in flight and never a history.
        self._in_flight: set[asyncio.Task[None]] = set()

    @property
    def table(self) -> Mail | None:
        """Where an org's own mail is kept. None on a box with no vault key to seal a password."""
        return self._mail

    @property
    def the_box_can_send(self) -> bool:
        """Whether THIS box has a mail server of its own: what /.well-known/pinecall carries."""
        return self._box is not None

    async def mailbox_for(self, org: str) -> Mailbox | None:
        """The org's own mail when it wired one, else the box's, else None: nothing is sent."""
        kept = None if self._mail is None else await self._mail.of(org)
        return self._box if kept is None else kept.mailbox

    # What a door answers as `mailed`. It says a letter was HANDED OVER, not that it arrived:
    # nothing that takes a second mail server's word for it can be known while a door is still
    # answering, and a door that waited to find out would be a door blocked on somebody else's
    # network. Where it went and what came of it is the row's standing, or the box's log.
    async def post(self, org: str, letter: Letter) -> bool:
        """Send it in the background. False when neither the org nor the box can send at all."""
        if await self.mailbox_for(org) is None:
            return False
        task = asyncio.ensure_future(self._posted(org, letter))
        self._in_flight.add(task)
        task.add_done_callback(self._in_flight.discard)
        return True

    async def sent(self, org: str, letter: Letter) -> str | None:
        """The same letter, waited for: None when it was taken, else what the server said. The
        one door that asks this is the test send, where a person is watching the spinner."""
        mailbox = await self.mailbox_for(org)
        if mailbox is None:
            return None
        try:
            await posted(mailbox, letter)
        except MailRefused as refused:
            await self._recorded(org, str(refused))
            return str(refused)
        await self._recorded(org, None)
        logger.info(POSTED, letter.subject, letter.to, mailbox.host)
        return None

    async def drained(self) -> None:
        """Wait for every letter in flight. The suite's; a door never waits for one."""
        while self._in_flight:
            await asyncio.gather(*tuple(self._in_flight), return_exceptions=True)

    # A task nobody awaits is a task whose exception nobody reads: a refusal is recorded above, and
    # anything else — the table unreachable while recording — is said in the log here, once, with
    # its stack, rather than as asyncio's "exception was never retrieved" at garbage collection.
    async def _posted(self, org: str, letter: Letter) -> None:
        """One background send: the outcome recorded, and nothing left to raise into the void."""
        try:
            said = await self.sent(org, letter)
        except Exception:
            logger.exception(NOT_POSTED, letter.subject, letter.to, "the outbox itself failed")
            return
        if said is not None:
            logger.warning(NOT_POSTED, letter.subject, letter.to, said)

    # Only against the org's OWN row. A letter that went through the box's mail says nothing
    # about a mailbox the org wired, and writing it there would tell an admin their server is
    # down when they have not wired one at all.
    async def _recorded(self, org: str, error: str | None) -> None:
        """What came of it, where the admin who wired the mailbox reads it."""
        if self._mail is not None:
            await self._mail.recorded(org, error)


def the_boxs_mailbox(settings: Settings) -> Mailbox | None:
    """PINECALL_SMTP_URL and PINECALL_MAIL_FROM as one mailbox; None when either is unset."""
    if not settings.smtp_url or not settings.mail_from:
        return None
    try:
        return a_mailbox_at(settings.smtp_url, settings.mail_from)
    except DeclarationRefused as refused:
        logger.error(NOT_A_MAIL_URL, "PINECALL_SMTP_URL", refused)
        return None


def outbox_for(settings: Settings, mail: Mail | None) -> Outbox:
    """The process's one outbox: this box's mail server, and the table an org wires its own in."""
    return Outbox(the_boxs_mailbox(settings), mail)
