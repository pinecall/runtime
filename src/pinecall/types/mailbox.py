"""Mailbox: the SMTP account a letter is posted through, and the URL a box declares one with."""

import re
from dataclasses import dataclass
from email.utils import parseaddr
from typing import Literal, cast
from urllib.parse import unquote, urlsplit

from pinecall.types.refusal import DeclarationRefused

# How the connection is protected. `starttls` is the ordinary one — port 587, a plain socket
# upgraded before anything is said — `tls` is implicit TLS on 465, and `none` is a plain socket,
# which is only ever right for a relay on the same machine.
type Security = Literal["starttls", "tls", "none"]
SECURITIES: frozenset[str] = frozenset({"starttls", "tls", "none"})

# The two schemes a URL says it with, each with the port and the security its world uses.
_SCHEMES: dict[str, tuple[int, Security]] = {"smtp": (587, "starttls"), "smtps": (465, "tls")}

# A host is a name or an address, and nothing with a space or a slash in it: what lands here is a
# line out of an operator's environment file, and `smtp.example.com/` is a typo worth naming.
_A_HOST = re.compile(r"^[A-Za-z0-9.\-_\[\]:]+$")

# Every address this runtime writes into an envelope is checked against this before the socket is
# opened: a header with a newline in it is how a From line becomes two headers (RFC 5322 §2.2).
AN_ADDRESS = re.compile(r"^[^\s@<>,;]+@[^\s@<>,;]+\.[^\s@<>,;]+$")

HIGHEST_PORT = 65535


@dataclass(frozen=True)
class Mailbox:
    """One SMTP account: where a letter is handed, who signs in there, and who it is from."""

    host: str
    port: int
    security: Security
    # Empty for a relay that asks for no credentials — a mail server of one's own on the same
    # box. The password is never read back out of any door; the row keeps a Fernet token.
    username: str
    password: str
    # The From header as it is written: "Pinecall <no-reply@example.com>", or a bare address.
    sender: str

    def __post_init__(self) -> None:
        if not _A_HOST.match(self.host):
            raise DeclarationRefused(f"{self.host!r} is not a host: a mail server is named bare")
        if not 1 <= self.port <= HIGHEST_PORT:
            raise DeclarationRefused(f"a port is 1 to {HIGHEST_PORT}, not {self.port}")
        if self.security not in SECURITIES:
            raise DeclarationRefused(
                f"a mailbox is secured by one of {sorted(SECURITIES)}, not {self.security!r}"
            )
        parse_address(self.sender)


def parse_address(written: str) -> str:
    """The address inside `Name <a@b.c>` or a bare one, or a refusal that shows what was read."""
    _, address = parseaddr(written)
    if not AN_ADDRESS.match(address):
        raise DeclarationRefused(f"{written!r} carries no email address")
    return address


def parse_security(word: str) -> Security:
    """The word as the closed type, or a refusal that lists the three."""
    if word not in SECURITIES:
        raise DeclarationRefused(
            f"a mailbox is secured by one of {sorted(SECURITIES)}, not {word!r}"
        )
    return cast("Security", word)


# What PINECALL_SMTP_URL is read into. A URL because it is ONE credential and a box keeps a
# credential as one file (infra/box): a host, a port, a user and a password in four variables
# would be four files, of which only one is a secret and all four would end up in argv.
#
# The credentials are split off at the LAST `@` rather than handed to urlsplit, because an SES SMTP
# password is base64 and carries `/` and `+`: a raw `/` ends a URL's authority, and the host would
# be read out of the middle of the password. So a password works percent-encoded, as a URL wants
# it, and pasted raw, as the console showed it. A refusal never repeats the URL: the password is in
# it, and a refusal is logged.
def parse_mailbox_url(url: str, sender: str) -> Mailbox:
    """`smtp://user:pass@host:587` or `smtps://…`, and who the letters are from."""
    scheme, separated, rest = url.strip().partition("://")
    if not separated or scheme not in _SCHEMES:
        raise DeclarationRefused("a mail URL starts smtp:// (STARTTLS) or smtps:// (implicit TLS)")
    port, security = _SCHEMES[scheme]
    userinfo, _, where = rest.rpartition("@")
    parts = urlsplit(f"{scheme}://{where.split('/', 1)[0]}")
    if not parts.hostname:
        raise DeclarationRefused("the mail URL names no mail server after its `@`")
    try:
        named = parts.port
    except ValueError as malformed:
        raise DeclarationRefused(f"the mail URL's port is not a number: {malformed}") from malformed
    username, _, password = userinfo.partition(":")
    return Mailbox(
        host=parts.hostname,
        port=named or port,
        security=security,
        username=unquote(username),
        password=unquote(password),
        sender=sender,
    )
