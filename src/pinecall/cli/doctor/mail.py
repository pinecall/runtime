"""The doctor's mail line: whether this box can post a letter, and `--mail-to` proving it does."""

import asyncio

from pinecall._settings import Settings, variable_of
from pinecall.mail import Letter, MailRefused, posted, the_boxs_mailbox
from pinecall.types import DeclarationRefused, Mailbox, an_address

# A box that posts no mail is not a box that is down: every door behaves as it did before mail
# existed, and an admin hands a link over by copying it out of the answer. So the line is advice,
# and it names the two variables rather than reading like an outage.
NO_MAIL = (
    "not configured — set {url} and {sender} to mail invitations and password resets; "
    "an admin hands the link over by copying it until then"
)

# Configured is not working: a password that was rotated at the relay sits in the credstore
# looking exactly like one that works, and the first to know is somebody who never got their
# invitation. `--mail-to` is the knock, and it is a real letter, so nobody sends one by accident.
CONFIGURED = (
    "{sender} through {host}:{port} ({security}) — `doctor --mail-to you@example.com` posts one"
)

SENT = "mail sent  {to} — taken by {host}:{port}"
REFUSED = "mail       {to} — {said}"
NOT_CONFIGURED = "mail       nothing to send it with: set {url} and {sender}"
NOT_AN_ADDRESS = "mail       {said}"


# A tuple and not a Result: the report's own row type lives in verbs.py, which reads this, and a
# module that read it back would close the circle. verbs.py wraps what this answers.
def the_mail_line(settings: Settings) -> tuple[bool, str]:
    """Whether this box has a mail server, and the sentence the report's row carries."""
    mailbox = the_boxs_mailbox(settings)
    if mailbox is None:
        return False, NO_MAIL.format(url=variable_of("smtp_url"), sender=variable_of("mail_from"))
    # Every field but the password, which is never printed, never logged and never in a report.
    return True, CONFIGURED.format(
        sender=mailbox.sender,
        host=mailbox.host,
        port=mailbox.port,
        security=mailbox.security,
    )


# The one thing in this CLI that leaves the machine on purpose, so it happens only when somebody
# types an address. It is printed under the report and is not a check: a person who asked for a
# letter is watching, and a box that cannot send one is still a box that carries calls.
def send_one_to(settings: Settings, to: str) -> int:
    """Post a test letter and say what came of it. 0 when a server took it, 1 when it did not."""
    mailbox = the_boxs_mailbox(settings)
    if mailbox is None:
        print(NOT_CONFIGURED.format(url=variable_of("smtp_url"), sender=variable_of("mail_from")))
        return 1
    try:
        address = an_address(to)
    except DeclarationRefused as refused:
        print(NOT_AN_ADDRESS.format(said=refused))
        return 1
    return asyncio.run(_posted(mailbox, address))


async def _posted(mailbox: Mailbox, to: str) -> int:
    """One letter through the box's own mail, waited for, and the server's own sentence on a no."""
    letter = Letter(
        to=to,
        subject="Pinecall test message",
        text="This is `pinecall-runtime doctor --mail-to`. Your mail server took it.",
        html="<p>This is <code>pinecall-runtime doctor --mail-to</code>. "
        "Your mail server took it.</p>",
    )
    try:
        await posted(mailbox, letter)
    except MailRefused as refused:
        print(REFUSED.format(to=to, said=refused))
        return 1
    print(SENT.format(to=to, host=mailbox.host, port=mailbox.port))
    return 0
