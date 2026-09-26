"""The transport: one letter handed to a generic SMTP server, off the loop, with a hard timeout."""

from __future__ import annotations

import asyncio
import smtplib
import ssl
from email.message import EmailMessage

from pinecall.errors import PinecallError
from pinecall.mail.letters import Letter
from pinecall.types import Mailbox

# What a door will wait for a mail server before it gives up on this letter. It is short because
# nothing waits on it — the door has already answered — and because a relay that has not said
# hello in ten seconds is a relay that is down, not one that is slow.
TIMEOUT_S = 10.0

# The library's own failures read as a class name and a tuple. What an admin needs is the sentence
# the SERVER said, which is what every SMTPResponseException carries and what this keeps.
REFUSED = "{host}:{port} refused it — {said}"
UNREACHABLE = "{host}:{port} did not answer — {said}"
TIMED_OUT = "{host}:{port} did not answer in {seconds:g}s"
# A header the library will not write — an org's name with a line break in it — is a letter that
# was never sent, said as one; the socket is not opened for it.
NOT_WRITTEN = "the letter could not be written — {said}"


class MailRefused(PinecallError):
    """The server would not take this letter, in its own words. Recorded; never raised at a door."""


# The whole of the vendor question, answered once: SES, Postmark, Mailgun and a mail server of
# one's own all speak this, so there is no SDK here and no vendor name anywhere in this package.
# smtplib is synchronous, so it runs in a thread — and it is given the timeout itself as well as
# being waited on with one, because cancelling `to_thread` does not stop the thread it started.
async def post(mailbox: Mailbox, letter: Letter, timeout: float = TIMEOUT_S) -> None:
    """Send it, or raise MailRefused carrying what the server said. Never blocks the loop."""
    try:
        message = build_email(mailbox, letter)
    except ValueError as unwritable:
        raise MailRefused(NOT_WRITTEN.format(said=unwritable)) from unwritable
    try:
        await asyncio.wait_for(asyncio.to_thread(_handed_over, mailbox, message, timeout), timeout)
    except TimeoutError as slow:
        raise MailRefused(
            TIMED_OUT.format(host=mailbox.host, port=mailbox.port, seconds=timeout)
        ) from slow


def _handed_over(mailbox: Mailbox, message: EmailMessage, timeout: float) -> None:
    """The blocking half: connect, protect it, sign in where there is a password, send, quit."""
    try:
        with _a_connection(mailbox, timeout) as server:
            if mailbox.security == "starttls":
                server.starttls(context=ssl.create_default_context())
            if mailbox.username:
                server.login(mailbox.username, mailbox.password)
            server.send_message(message)
    except smtplib.SMTPResponseException as said:
        raise MailRefused(_because(REFUSED, mailbox, _sentence(said))) from said
    except (OSError, smtplib.SMTPException) as down:
        raise MailRefused(
            _because(UNREACHABLE, mailbox, f"{type(down).__name__}: {down}")
        ) from down


def _a_connection(mailbox: Mailbox, timeout: float) -> smtplib.SMTP:
    """Implicit TLS opens protected; the other two open plain, and starttls upgrades one of them."""
    if mailbox.security == "tls":
        return smtplib.SMTP_SSL(
            mailbox.host, mailbox.port, timeout=timeout, context=ssl.create_default_context()
        )
    return smtplib.SMTP(mailbox.host, mailbox.port, timeout=timeout)


def build_email(mailbox: Mailbox, letter: Letter) -> EmailMessage:
    """The message on the wire: plain text, and the same letter as simple HTML beside it."""
    message = EmailMessage()
    message["From"] = mailbox.sender
    message["To"] = letter.to
    message["Subject"] = letter.subject
    # multipart/alternative, text first: a reader that shows one shows the one it can, and every
    # link in this package is written out in the text part so neither half is the only way in.
    message.set_content(letter.text)
    message.add_alternative(letter.html, subtype="html")
    return message


def _sentence(said: smtplib.SMTPResponseException) -> str:
    """`535 Authentication credentials invalid` — the code and the server's own line."""
    spoken = said.smtp_error
    written = spoken.decode("utf-8", "replace") if isinstance(spoken, bytes) else str(spoken)
    return f"{said.smtp_code} {written.strip()}"


# The host and the port are in every sentence and the credentials are in none of them: what an
# admin reads back at GET /v1/org/mail is this string, and a password in it would be a password
# in a door's answer.
def _because(shape: str, mailbox: Mailbox, said: str) -> str:
    """One refusal as it is recorded: where it was posted, and what came back."""
    return shape.format(host=mailbox.host, port=mailbox.port, said=said)
