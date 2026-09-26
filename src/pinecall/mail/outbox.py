"""Where a door hands a letter: the org's own mail, else the box's stored one, else its env's."""

from __future__ import annotations

import asyncio
import logging

from pinecall._settings import Settings
from pinecall.mail.box import TheBoxsMail, the_environments_mailbox
from pinecall.mail.brand import Brand, the_brand
from pinecall.mail.letters import Letter
from pinecall.mail.smtp import MailRefused, posted
from pinecall.orgs.box_settings import BoxSettings
from pinecall.orgs.org_mail import Mail
from pinecall.types import Mailbox

logger = logging.getLogger(__name__)

# What the process log says about a letter. The org's own mail records its outcome on its own row,
# where the admin who wired it reads it (GET /v1/org/mail); the box's STORED mail on its own, where
# the operator reads it (GET /v1/ops/mail); the environment's has no row, and is read here.
POSTED = "mailed %s to %s through %s"
NOT_POSTED = "could not mail %s to %s — %s"


# Whose mailbox a letter left by. It is what says where the outcome is written down.
THE_ORGS = "org"
THE_BOXS = "box"


class Outbox:
    """The one place a letter leaves by. It never raises: a door has already answered."""

    # The order is the whole policy. The ORG's own account first: its people's letters come from
    # its own domain. Then what the operator STORED from the console, which is newer than and
    # deliberately instead of the third: the line somebody left in the box's environment.
    def __init__(
        self, box: Mailbox | None, mail: Mail | None, settings: BoxSettings | None = None
    ) -> None:
        self._box = TheBoxsMail(box, settings)
        self._mail = mail
        self._settings = settings
        # Held, because a task nobody holds is a task the loop may collect mid-send. They are
        # discarded as they finish, so this is the letters in flight and never a history.
        self._in_flight: set[asyncio.Task[None]] = set()

    @property
    def table(self) -> Mail | None:
        """Where an org's own mail is kept. None on a box with no vault key to seal a password."""
        return self._mail

    @property
    def the_boxs(self) -> TheBoxsMail:
        """The box's own mailbox, stored or the environment's: what the operator's doors wire."""
        return self._box

    async def the_box_can_send(self) -> bool:
        """Whether THIS box has a mail server of its own, from either place: what
        /.well-known/pinecall carries."""
        return await self._box.of() is not None

    async def brand(self) -> Brand:
        """What the letters of this box are called and painted with, as the operator set it."""
        return await the_brand(self._settings)

    async def mailbox_for(self, org: str | None) -> Mailbox | None:
        """The org's own mail when it wired one, else the box's, else None: nothing is sent."""
        chosen = await self._chosen(org)
        return None if chosen is None else chosen[0]

    async def _chosen(self, org: str | None) -> tuple[Mailbox, str] | None:
        """The mailbox a letter of this org leaves by, and whose it is. No org is the box's own
        letter — the operator's test — which an org's account has no business carrying."""
        kept = None if self._mail is None or org is None else await self._mail.of(org)
        if kept is not None:
            return kept.mailbox, THE_ORGS
        boxs = await self._box.of()
        return None if boxs is None else (boxs.kept.mailbox, THE_BOXS)

    # What a door answers as `mailed`. It says a letter was HANDED OVER, not that it arrived:
    # nothing that takes a second mail server's word for it can be known while a door is still
    # answering, and a door that waited to find out would be a door blocked on somebody else's
    # network. Where it went and what came of it is the row's standing, or the box's log.
    async def post(self, org: str | None, letter: Letter) -> bool:
        """Send it in the background. False when neither the org nor the box can send at all.
        No org is the box's own letter — a sign-up's code, before any org exists."""
        if await self.mailbox_for(org) is None:
            return False
        task = asyncio.ensure_future(self._posted(org, letter))
        self._in_flight.add(task)
        task.add_done_callback(self._in_flight.discard)
        return True

    async def sent(self, org: str | None, letter: Letter) -> str | None:
        """The same letter, waited for: None when it was taken, else what the server said. The
        doors that ask this are the test sends, where a person is watching the spinner; with no
        org it is the BOX's own mailbox that is tested, whatever any org wired."""
        chosen = await self._chosen(org)
        if chosen is None:
            return None
        mailbox, whose = chosen
        try:
            await posted(mailbox, letter)
        except MailRefused as refused:
            await self._recorded(org, whose, str(refused))
            return str(refused)
        await self._recorded(org, whose, None)
        logger.info(POSTED, letter.subject, letter.to, mailbox.host)
        return None

    async def drained(self) -> None:
        """Wait for every letter in flight. The suite's; a door never waits for one."""
        while self._in_flight:
            await asyncio.gather(*tuple(self._in_flight), return_exceptions=True)

    # A task nobody awaits is a task whose exception nobody reads: a refusal is recorded above, and
    # anything else — the table unreachable while recording — is said in the log here, once, with
    # its stack, rather than as asyncio's "exception was never retrieved" at garbage collection.
    async def _posted(self, org: str | None, letter: Letter) -> None:
        """One background send: the outcome recorded, and nothing left to raise into the void."""
        try:
            said = await self.sent(org, letter)
        except Exception:
            logger.exception(NOT_POSTED, letter.subject, letter.to, "the outbox itself failed")
            return
        if said is not None:
            logger.warning(NOT_POSTED, letter.subject, letter.to, said)

    # Against the row of the mailbox it LEFT BY, and no other. A letter that went through the
    # box's mail says nothing about a mailbox the org wired, and writing it there would tell an
    # admin their server is down when they have not wired one at all.
    async def _recorded(self, org: str | None, whose: str, error: str | None) -> None:
        """What came of it, where whoever wired that mailbox reads it."""
        if whose == THE_BOXS:
            await self._box.recorded(error)
        elif self._mail is not None and org is not None:
            await self._mail.recorded(org, error)


def outbox_for(settings: Settings, mail: Mail | None, box: BoxSettings | None = None) -> Outbox:
    """The process's one outbox: the org table, what the operator stored, and the environment."""
    return Outbox(the_environments_mailbox(settings), mail, box)
