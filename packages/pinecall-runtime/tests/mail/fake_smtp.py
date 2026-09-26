"""A mail server this process runs on the loopback: enough SMTP to take a letter, or refuse one."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
from email import message_from_string, policy
from email.message import EmailMessage
from types import TracebackType
from typing import Any

from pinecall.types import Mailbox

# The loopback and a port the kernel picks: a suite that bound a fixed one would fail on the
# machine where something else has it, and the point of this server is that it is nobody else's.
LOOPBACK = "127.0.0.1"

# What is said to a client that got it right, and the two refusals a case asks for by name. They
# are real SMTP sentences, because what an admin reads back at GET /v1/org/mail is this text.
BAD_CREDENTIALS = "535 5.7.8 Authentication credentials invalid"
NOT_AUTHORIZED = "554 Message rejected: Email address is not verified"


@dataclass(frozen=True)
class Envelope:
    """One letter as the server took it: who it says it is from, who it is for, and its bytes."""

    sender: str
    recipients: tuple[str, ...]
    data: str

    @property
    def message(self) -> EmailMessage:
        """The letter parsed, so a case reads a header or a part instead of a string. The modern
        policy, so `Subject` comes back as the words and not as `=?utf-8?q?…?=`."""
        parsed = message_from_string(self.data, policy=policy.default)
        assert isinstance(parsed, EmailMessage)
        return parsed

    @property
    def parts(self) -> dict[str, str]:
        """Its bodies by content type: `text/plain` and `text/html` for every letter here."""
        parsed: Any = self.message
        walked: list[Any] = list(parsed.walk()) if parsed.is_multipart() else [parsed]
        return {
            str(part.get_content_type()): str(part.get_content())
            for part in walked
            if not part.is_multipart()
        }


@dataclass
class FakeSmtp:
    """The server: what it took, what it will refuse, and the mailbox that reaches it."""

    took: list[Envelope] = field(default_factory=list[Envelope])
    # A password that was rotated at the relay and never here, and an address the account may not
    # send from — the two failures an operator actually meets, at the two moments they happen.
    refuses_the_password: bool = False
    refuses_the_letter: bool = False
    port: int = 0
    _server: asyncio.Server | None = None

    async def __aenter__(self) -> FakeSmtp:
        self._server = await asyncio.start_server(self._spoken_to, LOOPBACK, 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(
        self,
        kind: type[BaseException] | None,
        raised: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        assert self._server is not None
        self._server.close()
        await self._server.wait_closed()

    @property
    def host(self) -> str:
        """Where it is listening. Beside `port`, this is what an org wires its own mail to."""
        return LOOPBACK

    def mailbox(self, sender: str = "Pinecall <no-reply@box.test>", user: str = "AKIA") -> Mailbox:
        """A mailbox pointed at this server. Plain, because a fake certificate proves nothing."""
        return Mailbox(
            host=LOOPBACK,
            port=self.port,
            security="none",
            username=user,
            password="s3cret",
            sender=sender,
        )

    # One connection, one letter, line by line. It answers only the verbs smtplib speaks on the
    # way to a send — there is no relaying here and nothing is queued.
    async def _spoken_to(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """The whole conversation: hello, sign in, from, to, data, quit."""
        sender = ""
        recipients: list[str] = []
        _say(writer, "220 fake.test ESMTP")
        while line := await _line(reader):
            verb, _, rest = line.partition(" ")
            word = verb.upper()
            if word in ("EHLO", "HELO"):
                _say(
                    writer, "250-fake.test\r\n250-SIZE 33554432\r\n250-AUTH PLAIN LOGIN\r\n250 HELP"
                )
            elif word == "AUTH":
                # Said and not hung up on: a client is told no and then says QUIT, which is
                # what makes the refusal a SENTENCE rather than a reset connection.
                _say(writer, BAD_CREDENTIALS if self.refuses_the_password else "235 2.7.0 in")
            elif word == "MAIL":
                sender = _inside(rest)
                _say(writer, "250 2.1.0 Ok")
            elif word == "RCPT":
                recipients.append(_inside(rest))
                _say(writer, "250 2.1.5 Ok")
            elif word == "DATA":
                _say(writer, "354 End data with <CR><LF>.<CR><LF>")
                body = await _until_the_dot(reader)
                if self.refuses_the_letter:
                    _say(writer, NOT_AUTHORIZED)
                    break
                self.took.append(Envelope(sender, tuple(recipients), body))
                _say(writer, "250 2.0.0 Ok: queued")
            elif word == "QUIT":
                _say(writer, "221 2.0.0 Bye")
                break
            else:
                _say(writer, "502 5.5.2 Not implemented")
        writer.close()


def a_plain_login(said: str) -> tuple[str, str]:
    """What `AUTH PLAIN <base64>` carried: the user and the password, for a case that asserts it."""
    _, _, encoded = said.partition(" ")
    _, user, password = base64.b64decode(encoded).decode().split("\0")
    return user, password


def _say(writer: asyncio.StreamWriter, line: str) -> None:
    """One reply, CRLF-terminated as the protocol wants it."""
    writer.write(f"{line}\r\n".encode())


async def _line(reader: asyncio.StreamReader) -> str:
    """One command, or the empty string when the client hung up."""
    return (await reader.readline()).decode("utf-8", "replace").strip()


# The letter's own lines are NOT stripped: a header folded onto a second line is continued by the
# whitespace at the front of it, and a parser handed one without it reads two broken headers.
async def _until_the_dot(reader: asyncio.StreamReader) -> str:
    """The letter itself, unstuffed: every line until one that is a single dot."""
    lines: list[str] = []
    while (line := (await reader.readline()).decode("utf-8", "replace").rstrip("\r\n")) != ".":
        lines.append(line[1:] if line.startswith("..") else line)
    return "\n".join(lines)


def _inside(rest: str) -> str:
    """`FROM:<a@b.c> SIZE=42` — the address between the angle brackets, and nothing else."""
    _, _, after = rest.partition("<")
    return after.partition(">")[0]
