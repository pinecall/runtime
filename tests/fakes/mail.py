"""A mail server the outbox posts to, and the postbox it lands in."""

import smtplib
from dataclasses import dataclass, field
from email.message import Message
from typing import ClassVar, Self, override


@dataclass
class Postbox:
    """What a fake mail server was told: who signed in, what was sent, and what it refuses."""

    hosts: list[tuple[str, int]] = field(default_factory=list[tuple[str, int]])
    logins: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])
    starttls: int = 0
    sent: list[Message] = field(default_factory=list[Message])
    # A reply code and sentence the server answers the login or the letter with.
    refuses_login: tuple[int, str] | None = None
    refuses_letter: tuple[int, str] | None = None


# Put in smtplib's place by the test, with the postbox it answers from.
class MailServer(smtplib.SMTP):
    """A mail server that answers as its postbox says, and keeps what it was sent."""

    postbox: ClassVar[Postbox] = Postbox()

    @override
    def __init__(self, host: str = "", port: int = 0, **_: object) -> None:
        self.postbox.hosts.append((host, port))

    @override
    def __enter__(self) -> Self:
        return self

    @override
    def __exit__(self, *_: object) -> None:
        return

    @override
    def starttls(self, *_: object, **__: object) -> tuple[int, bytes]:
        self.postbox.starttls += 1
        return 220, b"ready"

    @override
    def login(
        self, user: str, password: str, *, initial_response_ok: bool = True
    ) -> tuple[int, bytes]:
        if self.postbox.refuses_login is not None:
            code, data = self.postbox.refuses_login
            raise smtplib.SMTPAuthenticationError(code, data.encode())
        self.postbox.logins.append((user, password))
        return 235, b"ok"

    @override
    def send_message(self, msg: Message, *_: object, **__: object) -> dict[str, tuple[int, bytes]]:
        if self.postbox.refuses_letter is not None:
            code, data = self.postbox.refuses_letter
            raise smtplib.SMTPDataError(code, data.encode())
        self.postbox.sent.append(msg)
        return {}
