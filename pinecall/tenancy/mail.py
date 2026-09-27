"""Mail: the mailboxes a letter goes out through, the box's brand, the five letters, and SMTP."""

import asyncio
import logging
import re
import smtplib
import ssl
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import parseaddr
from html import escape
from typing import Literal
from urllib.parse import unquote, urlsplit

from cryptography.fernet import MultiFernet
from psycopg.types.json import Jsonb

from pinecall.domain.errors import DeclarationRefused, UpstreamFailed
from pinecall.domain.types import AN_ADDRESS, Json
from pinecall.postgres.pool import Pool
from pinecall.tenancy.vault import opened, sealed

logger = logging.getLogger(__name__)

# ── mailboxes ──

# starttls on 587, implicit TLS on 465; none only for a relay on the same machine.
type Security = Literal["starttls", "tls", "none"]
# Where a mailbox was read from: the org's own, the one the operator stored, the environment's.
type Source = Literal["org", "stored", "environment"]

SCHEMES: Mapping[str, tuple[int, Security]] = {"smtp": (587, "starttls"), "smtps": (465, "tls")}
# No spaces or slashes: it catches `smtp.example.com/`.
A_HOST = re.compile(r"^[A-Za-z0-9.\-_\[\]:]+$")
HIGHEST_PORT = 65535
TIMEOUT_S = 10.0

NOT_A_MAIL_URL = "a mail URL starts smtp:// (STARTTLS) or smtps:// (implicit TLS)"
NO_SERVER = "the mail URL names no mail server after its `@`"
NO_ADDRESS = "{written!r} carries no email address"
# Never the URL itself in a refusal: it holds the password.
REFUSED = "{host}:{port} refused it: {said}"
UNREACHABLE = "{host}:{port} did not answer: {said}"
TIMED_OUT = "{host}:{port} did not answer in {seconds:g}s"
NOT_WRITTEN = "the letter could not be written: {said}"

BOX_MAIL = "mail"
BRAND = "brand"

ORG_MAIL = """
SELECT host, port, security, username, sender, ciphertext, verified_at, last_error
FROM org_mail WHERE org = %(org)s
"""
PUT_ORG_MAIL = """
INSERT INTO org_mail (org, host, port, security, username, ciphertext, sender)
VALUES (%(org)s, %(host)s, %(port)s, %(security)s, %(username)s, %(ciphertext)s, %(sender)s)
ON CONFLICT (org) DO UPDATE SET
    host = excluded.host, port = excluded.port, security = excluded.security,
    username = excluded.username, ciphertext = excluded.ciphertext, sender = excluded.sender,
    verified_at = NULL, last_error = NULL, set_at = now()
"""
DROP_ORG_MAIL = "DELETE FROM org_mail WHERE org = %(org)s RETURNING org"
ORG_MAIL_WENT = """
UPDATE org_mail SET last_error = %(error)s,
       verified_at = CASE WHEN %(error)s::text IS NULL THEN now() ELSE verified_at END
WHERE org = %(org)s
"""
SETTING = "SELECT value, ciphertext FROM box_settings WHERE name = %(name)s"
PUT_SETTING = """
INSERT INTO box_settings (name, value, ciphertext) VALUES (%(name)s, %(value)s, %(ciphertext)s)
ON CONFLICT (name) DO UPDATE SET value = excluded.value, ciphertext = excluded.ciphertext,
                                 set_at = now()
"""
DROP_SETTING = "DELETE FROM box_settings WHERE name = %(name)s RETURNING name"
# The outcome is kept inside the box's row, beside what it names.
BOX_MAIL_WENT = """
UPDATE box_settings SET value = value || %(went)s WHERE name = %(name)s
"""

# ── the brand ──

NAME = "Pinecall"
ACCENT = "#5b3df5"
# Written into a style attribute, so six hex digits and nothing else.
A_BRAND_COLOUR = re.compile(r"^#[0-9a-fA-F]{6}$")
# The name goes into a subject line, where a line break would start another header.
LONGEST_NAME = 60
NOT_A_NAME = f"a brand's name is one line of at most {LONGEST_NAME} characters"
NOT_A_COLOUR = "{said!r} is not an accent: a colour is #rrggbb, six hex digits"
NOT_A_LOGO = (
    "{said!r} is not a logo: an https:// URL of an image, since a mail client fetches it from "
    "wherever the reader is and blocks plain http"
)

# ── the letters ──

CARD = "/invitations/{token}"
ONCE = "This link opens once."
NOBODY_ASKED = "If you did not ask for this, nothing has changed and you can ignore this message."
FROM_THEM = "Sent by {org} through {brand}"
DIES_ON = "{sent} · this link opens once and dies on {date}"
OPEN_IT = "Open it in a browser"

# Inline styles only: mail clients read little CSS. No webfont and no image but the operator's
# logo, since a remote request tells the sender when and where a letter was opened.
WASH = "#f7f6fa"
INK = "#101014"
MUTED = "#6b6975"
HAIRLINE = "#eeedf2"
FONT = "Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
MONO = "SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace"
WIDTH = 560
LOGO_HEIGHT = 28


@dataclass(frozen=True)
class Mailbox:
    """An SMTP account: the server, the credentials, and who the letters are from."""

    host: str
    port: int
    security: Security
    # Empty for a relay that asks for nothing. The password is kept sealed, never shown.
    username: str
    password: str
    sender: str

    def __post_init__(self) -> None:
        if not A_HOST.match(self.host):
            raise DeclarationRefused(f"{self.host!r} is not a host: a mail server is named bare")
        if not 1 <= self.port <= HIGHEST_PORT:
            raise DeclarationRefused(f"a port is 1 to {HIGHEST_PORT}, not {self.port}")
        address_of(self.sender)


@dataclass(frozen=True)
class Standing:
    """A mailbox, how its last letter went, and where it was read from."""

    mailbox: Mailbox
    source: Source
    verified_at: datetime | None = None
    last_error: str | None = None


@dataclass(frozen=True)
class Brand:
    """What the letters are called and painted with."""

    name: str = NAME
    # None fetches no image at all.
    logo_url: str | None = None
    accent: str = ACCENT

    def __post_init__(self) -> None:
        if not self.name.strip() or len(self.name) > LONGEST_NAME or "\n" in self.name:
            raise DeclarationRefused(NOT_A_NAME)
        if not A_BRAND_COLOUR.match(self.accent):
            raise DeclarationRefused(NOT_A_COLOUR.format(said=self.accent))
        if self.logo_url is not None and not _an_https_url(self.logo_url):
            raise DeclarationRefused(NOT_A_LOGO.format(said=self.logo_url))


@dataclass(frozen=True)
class Letter:
    """A letter ready to go: to whom, its subject, and the same words as text and as HTML."""

    to: str
    subject: str
    text: str
    html: str


@dataclass(frozen=True)
class Wording:
    """What a letter with a link says: its subject, its title, its sentences and its button."""

    subject: str
    title: str
    said: tuple[str, ...]
    label: str
    # Nobody may have asked for it, so it says what to do then.
    unbidden: bool = False


@dataclass(frozen=True)
class Link:
    """What a letter with a link says: the org, who sent it, the link, and when it dies."""

    org: str
    link: str
    by: str | None = None
    dies: datetime | None = None


def address_of(written: str) -> str:
    """The address in `Name <a@b.c>` or a bare one; refused when there is none."""
    _, address = parseaddr(written)
    if not AN_ADDRESS.match(address):
        raise DeclarationRefused(NO_ADDRESS.format(written=written))
    return address


# Split at the LAST `@`: an SES password is base64 and may hold a raw `/`, which would end a URL's
# authority for urlsplit.
def parse_mailbox_url(url: str, sender: str) -> Mailbox:
    """`smtp://user:pass@host:587` or `smtps://…`, and who letters are from, as a mailbox."""
    scheme, separated, rest = url.strip().partition("://")
    if not separated or scheme not in SCHEMES:
        raise DeclarationRefused(NOT_A_MAIL_URL)
    port, security = SCHEMES[scheme]
    userinfo, _, where = rest.rpartition("@")
    parts = urlsplit(f"{scheme}://{where.split('/', 1)[0]}")
    if not parts.hostname:
        raise DeclarationRefused(NO_SERVER)
    try:
        named = parts.port
    except ValueError:
        raise DeclarationRefused("the mail URL's port is not a number") from None
    username, _, password = userinfo.partition(":")
    return Mailbox(
        host=parts.hostname,
        port=named or port,
        security=security,
        username=unquote(username),
        password=unquote(password),
        sender=sender,
    )


async def mail_of(pool: Pool, vault: MultiFernet, org: str) -> Standing | None:
    """The org's own mailbox and how its last letter went."""
    async with pool.connection() as connection:
        row = await (await connection.execute(ORG_MAIL, {"org": org})).fetchone()
    if row is None:
        return None
    return _standing(row, opened(vault, row["ciphertext"]), "org")


async def put_mail(pool: Pool, vault: MultiFernet, org: str, mailbox: Mailbox) -> None:
    """Keep the org's own mailbox, the password sealed; how it went is forgotten."""
    values = {**_fields(mailbox), "org": org, "ciphertext": sealed(vault, mailbox.password)}
    async with pool.connection() as connection:
        await connection.execute(PUT_ORG_MAIL, values)


async def drop_mail(pool: Pool, org: str) -> bool:
    """Forget the org's own mailbox; whether it had one."""
    async with pool.connection() as connection:
        return await (await connection.execute(DROP_ORG_MAIL, {"org": org})).fetchone() is not None


async def box_mail_of(
    pool: Pool, vault: MultiFernet, environment: Mailbox | None
) -> Standing | None:
    """The box's mailbox: the one the operator stored, else the environment's."""
    async with pool.connection() as connection:
        row = await (await connection.execute(SETTING, {"name": BOX_MAIL})).fetchone()
    stored = None if row is None or row["ciphertext"] is None else row
    if stored is not None:
        found = _standing(stored["value"], opened(vault, stored["ciphertext"]), "stored")
        if found is not None:
            return found
    return None if environment is None else Standing(environment, "environment")


async def put_box_mail(pool: Pool, vault: MultiFernet, mailbox: Mailbox) -> None:
    """Keep the mailbox the box sends through, over the environment's; how it went is forgotten."""
    values = {
        "name": BOX_MAIL,
        "value": Jsonb(_fields(mailbox)),
        "ciphertext": sealed(vault, mailbox.password),
    }
    async with pool.connection() as connection:
        await connection.execute(PUT_SETTING, values)


async def drop_box_mail(pool: Pool) -> bool:
    """Forget the box's stored mailbox, back to the environment's; whether one was stored."""
    async with pool.connection() as connection:
        dropped = await connection.execute(DROP_SETTING, {"name": BOX_MAIL})
        return await dropped.fetchone() is not None


async def brand_of(pool: Pool) -> Brand:
    """The box's brand; Pinecall's when nobody set one, or when the row does not read."""
    async with pool.connection() as connection:
        row = await (await connection.execute(SETTING, {"name": BRAND})).fetchone()
    if row is None:
        return Brand()
    value = row["value"]
    try:
        return Brand(
            name=str(value.get("name") or NAME),
            logo_url=value.get("logo_url") or None,
            accent=str(value.get("accent") or ACCENT),
        )
    except DeclarationRefused:
        logger.warning("the box's brand does not read: letters go out as Pinecall's")
        return Brand()


async def put_brand(pool: Pool, brand: Brand) -> None:
    """Keep the box's brand."""
    value = {"name": brand.name, "logo_url": brand.logo_url, "accent": brand.accent}
    async with pool.connection() as connection:
        await connection.execute(
            PUT_SETTING, {"name": BRAND, "value": Jsonb(value), "ciphertext": None}
        )


# None keeps a field; an empty string sets it back to the default, the one way to clear a logo.
def apply_brand(
    brand: Brand, *, name: str | None = None, logo_url: str | None = None, accent: str | None = None
) -> Brand:
    """The brand with what was named replaced."""
    return Brand(
        name=brand.name if name is None else (name.strip() or NAME),
        logo_url=brand.logo_url if logo_url is None else (logo_url.strip() or None),
        accent=brand.accent if accent is None else (accent.strip().lower() or ACCENT),
    )


def card_link(base: str, token: str) -> str:
    """The console's card for a token, at the name this gateway answers to."""
    return f"{base.rstrip('/')}{CARD.format(token=token)}"


def invitation_letter(to: str, link: Link, brand: Brand) -> Letter:
    """The letter that invites a person into an org."""
    wording = Wording(
        subject=f"You're invited to {link.org} on {brand.name}",
        title="Join your team",
        said=(
            f"{link.by} has invited you to join {link.org} on {brand.name}.",
            "Choose a password and you are in.",
        ),
        label="Accept the invitation",
    )
    return _linked(to, link, brand, wording)


def reset_letter(to: str, link: Link, brand: Brand) -> Letter:
    """The letter an admin's reset of a member's password sends."""
    wording = Wording(
        subject=f"Reset your {brand.name} password",
        title="Set a new password",
        said=(
            f"{link.by} has reset your password for {link.org}.",
            "Choose a new one to sign in again.",
        ),
        label="Choose a password",
    )
    return _linked(to, link, brand, wording)


def forgotten_password_letter(to: str, link: Link, brand: Brand) -> Letter:
    """The letter a forgotten password sends, which nobody may have asked for."""
    wording = Wording(
        subject=f"Reset your {brand.name} password",
        title="Reset your password",
        said=(f"Somebody asked to reset the password for this address at {link.org}.",),
        label="Choose a password",
        unbidden=True,
    )
    return _linked(to, link, brand, wording)


def probe_letter(to: str, brand: Brand) -> Letter:
    """The test letter that proves a mailbox sends."""
    said = (
        f"This is a test message from {brand.name}. Your mail server took it, so the letters "
        "this gateway sends will reach you."
    )
    body = _heading("It works") + _paragraph(said)
    return Letter(to, f"{brand.name} test message", said, _framed(said, body, brand.name, brand))


# The code rides the preheader, never the subject: the outbox logs subjects, and a logged code is
# a code anybody reading the log could spend.
def signup_code_letter(to: str, code: str, person: str, brand: Brand) -> Letter:
    """The letter that carries a sign-up's six digits."""
    asked = f"Hi {person}, enter this code to finish setting up your {brand.name} account:"
    dies = "This code expires in 15 minutes."
    unbidden = f"If you did not create a {brand.name} account, ignore this email."
    footer = f"Sent by {brand.name}"
    body = (
        _heading("Confirm your email")
        + _paragraph(asked)
        + _code(code)
        + _small(dies)
        + _small(unbidden)
    )
    text = f"Confirm your email\n\n{asked}\n\n{code}\n\n{dies}\n\n{unbidden}\n\n{footer}"
    preheader = f"Your {brand.name} verification code is {code}"
    return Letter(
        to, f"Confirm your {brand.name} email", text, _framed(preheader, body, footer, brand)
    )


# smtplib gets a timeout of its own: cancelling the thread's future does not stop the thread.
async def post(mailbox: Mailbox, letter: Letter, *, within_s: float = TIMEOUT_S) -> None:
    """Hand the letter to the mail server; its own reply when it refuses."""
    try:
        message = _message(mailbox, letter)
    except ValueError as unwritable:
        raise UpstreamFailed(NOT_WRITTEN.format(said=unwritable)) from unwritable
    try:
        await asyncio.wait_for(asyncio.to_thread(_handed, mailbox, message, within_s), within_s)
    except TimeoutError:
        said = TIMED_OUT.format(host=mailbox.host, port=mailbox.port, seconds=within_s)
        raise UpstreamFailed(said) from None


class Outbox:
    """Every letter the gateway sends, through the org's mailbox or the box's, in the background."""

    def __init__(self, pool: Pool, vault: MultiFernet, environment: Mailbox | None) -> None:
        """An outbox with nothing in flight."""
        self.pool = pool
        self.vault = vault
        self.environment = environment
        # Held so the loop cannot collect a letter halfway out.
        self.in_flight: set[asyncio.Task[None]] = set()

    async def mailbox_for(self, org: str | None) -> Standing | None:
        """The org's mailbox, else the box's; with no org, only the box's."""
        own = None if org is None else await mail_of(self.pool, self.vault, org)
        return own or await box_mail_of(self.pool, self.vault, self.environment)

    # Queued, not delivered: a request never waits on somebody's mail server.
    async def post(self, org: str | None, letter: Letter) -> bool:
        """Send the letter in the background; False when nothing here can send it."""
        if await self.mailbox_for(org) is None:
            return False
        task = asyncio.create_task(self._posted(org, letter))
        self.in_flight.add(task)
        task.add_done_callback(self.in_flight.discard)
        return True

    async def sent(self, org: str | None, letter: Letter) -> str | None:
        """Send and wait, as a test letter does: None when it went, else the server's reply."""
        standing = await self.mailbox_for(org)
        if standing is None:
            return None
        try:
            await post(standing.mailbox, letter)
        except UpstreamFailed as refused:
            await self._went(org, standing.source, str(refused))
            return str(refused)
        await self._went(org, standing.source, None)
        logger.info("mailed %s through %s", letter.subject, standing.mailbox.host)
        return None

    async def drained(self) -> None:
        """Wait for every letter in flight."""
        while self.in_flight:
            await asyncio.gather(*self.in_flight, return_exceptions=True)

    # A failure is said here: asyncio would say it only when the task is collected.
    async def _posted(self, org: str | None, letter: Letter) -> None:
        said = await self.sent(org, letter)
        if said is not None:
            logger.warning("could not mail %s: %s", letter.subject, said)

    # Kept on the mailbox the letter went through, never on another.
    async def _went(self, org: str | None, source: Source, error: str | None) -> None:
        async with self.pool.connection() as connection:
            if source == "org":
                await connection.execute(ORG_MAIL_WENT, {"org": org, "error": error})
            elif source == "stored":
                went: dict[str, Json] = {"last_error": error}
                if error is None:
                    went["verified_at"] = datetime.now(UTC).isoformat()
                await connection.execute(BOX_MAIL_WENT, {"name": BOX_MAIL, "went": Jsonb(went)})


def _standing(fields: Mapping[str, object], password: Json, source: Source) -> Standing | None:
    if not isinstance(password, str):
        return None
    try:
        mailbox = Mailbox(
            host=str(fields["host"]),
            port=int(str(fields["port"])),
            security=_security(str(fields["security"])),
            username=str(fields["username"]),
            password=password,
            sender=str(fields["sender"]),
        )
    except (KeyError, ValueError, DeclarationRefused):
        logger.warning("a stored mailbox does not read: it is left unused")
        return None
    verified = fields.get("verified_at")
    if isinstance(verified, str):
        verified = datetime.fromisoformat(verified)
    error = fields.get("last_error")
    return Standing(
        mailbox=mailbox,
        source=source,
        verified_at=verified if isinstance(verified, datetime) else None,
        last_error=error if isinstance(error, str) else None,
    )


def _security(word: str) -> Security:
    match word:
        case "starttls" | "tls" | "none":
            return word
        case _:
            raise DeclarationRefused(f"a mailbox is secured by starttls, tls or none, not {word!r}")


def _fields(mailbox: Mailbox) -> dict[str, Json]:
    return {
        "host": mailbox.host,
        "port": mailbox.port,
        "security": mailbox.security,
        "username": mailbox.username,
        "sender": mailbox.sender,
    }


def _handed(mailbox: Mailbox, message: EmailMessage, timeout: float) -> None:
    try:
        with _connected(mailbox, timeout) as server:
            if mailbox.security == "starttls":
                server.starttls(context=ssl.create_default_context())
            if mailbox.username:
                server.login(mailbox.username, mailbox.password)
            server.send_message(message)
    except smtplib.SMTPResponseException as said:
        spoken = (
            said.smtp_error.decode("utf-8", "replace")
            if isinstance(said.smtp_error, bytes)
            else str(said.smtp_error)
        )
        reply = f"{said.smtp_code} {spoken.strip()}"
        raise UpstreamFailed(
            REFUSED.format(host=mailbox.host, port=mailbox.port, said=reply)
        ) from said
    except (OSError, smtplib.SMTPException) as down:
        said = f"{type(down).__name__}: {down}"
        raise UpstreamFailed(
            UNREACHABLE.format(host=mailbox.host, port=mailbox.port, said=said)
        ) from down


def _connected(mailbox: Mailbox, timeout: float) -> smtplib.SMTP:
    if mailbox.security == "tls":
        context = ssl.create_default_context()
        return smtplib.SMTP_SSL(mailbox.host, mailbox.port, timeout=timeout, context=context)
    return smtplib.SMTP(mailbox.host, mailbox.port, timeout=timeout)


# The text first: a client that shows one part shows it, and every link is written out in it.
def _message(mailbox: Mailbox, letter: Letter) -> EmailMessage:
    message = EmailMessage()
    message["From"] = mailbox.sender
    message["To"] = letter.to
    message["Subject"] = letter.subject
    message.set_content(letter.text)
    message.add_alternative(letter.html, subtype="html")
    return message


# Text and HTML come from the same sentences, so the two halves never disagree.
def _linked(to: str, link: Link, brand: Brand, wording: Wording) -> Letter:
    quiet = [ONCE, *([NOBODY_ASKED] if wording.unbidden else [])]
    body = (
        _heading(wording.title)
        + "".join(_paragraph(one) for one in wording.said)
        + _button(wording.label, link.link, brand.accent)
        + _fallback(link.link)
        + "".join(_small(one) for one in quiet)
    )
    sent = FROM_THEM.format(org=link.org, brand=brand.name)
    footer = sent if link.dies is None else DIES_ON.format(sent=sent, date=_day(link.dies))
    opened_it = f"{OPEN_IT}:\n\n{link.link}"
    text = "\n\n".join([wording.title, *wording.said, opened_it, *quiet, footer])
    return Letter(to, wording.subject, text, _framed(wording.said[0], body, footer, brand))


def _day(moment: datetime) -> str:
    return f"{moment.day} {moment.strftime('%B')} {moment.year}"


def _heading(text: str) -> str:
    return (
        f'<h1 style="margin:0 0 14px;font-family:{FONT};font-size:21px;line-height:1.3;'
        f'font-weight:650;letter-spacing:-0.02em;color:{INK};">{escape(text)}</h1>'
    )


def _paragraph(text: str) -> str:
    return (
        f'<p style="margin:0 0 16px;font-family:{FONT};font-size:15px;line-height:1.6;'
        f'color:{INK};">{escape(text)}</p>'
    )


def _small(text: str) -> str:
    return (
        f'<p style="margin:0 0 8px;font-family:{FONT};font-size:13px;line-height:1.5;'
        f'color:{MUTED};">{escape(text)}</p>'
    )


# A table: Outlook draws an inline-block link wrong.
def _button(label: str, href: str, accent: str) -> str:
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" style="margin:6px 0 18px;">'
        f'<tr><td><a href="{escape(href, quote=True)}" style="display:inline-block;'
        f"background:{accent};color:#ffffff;font-family:{FONT};font-size:15px;font-weight:600;"
        f'text-decoration:none;padding:12px 22px;line-height:18px;border-radius:9px;">'
        f"{escape(label)}</a></td></tr></table>"
    )


def _fallback(href: str) -> str:
    return (
        f'<p style="margin:0 0 16px;font-family:{FONT};font-size:13px;line-height:1.5;'
        f'color:{MUTED};word-break:break-all;">{escape(href)}</p>'
    )


# The left padding is the letter spacing, which also trails the last digit: the code stays centred.
def _code(code: str) -> str:
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="margin:8px 0 20px;"><tr><td align="center" style="background:{WASH};'
        f'border:1px solid {HAIRLINE};border-radius:12px;padding:22px 16px;">'
        f'<div style="font-family:{MONO};font-size:34px;line-height:1;font-weight:600;'
        f'letter-spacing:10px;padding-left:10px;color:{INK};">{escape(code)}</div>'
        "</td></tr></table>"
    )


def _framed(preheader: str, body: str, footer: str, brand: Brand) -> str:
    logo = (
        ""
        if brand.logo_url is None
        else '<tr><td style="padding:0 6px 16px;">'
        f'<img src="{escape(brand.logo_url, quote=True)}" alt="{escape(brand.name, quote=True)}" '
        f'height="{LOGO_HEIGHT}" style="display:block;height:{LOGO_HEIGHT}px;width:auto;border:0;">'
        "</td></tr>"
    )
    return (
        '<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="color-scheme" content="light only"></head>'
        f'<body style="margin:0;padding:0;background:{WASH};">'
        '<div style="display:none;max-height:0;overflow:hidden;opacity:0;">'
        f"{escape(preheader)}</div>"
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="background:{WASH};padding:36px 14px;"><tr><td align="center">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="max-width:{WIDTH}px;">{logo}'
        f'<tr><td style="background:#ffffff;border:1px solid {HAIRLINE};border-radius:14px;'
        f'padding:30px 30px 24px;">{body}</td></tr>'
        f'<tr><td style="padding:18px 6px 0;font-family:{FONT};font-size:12px;line-height:1.6;'
        f'color:{MUTED};">{escape(footer)}</td></tr>'
        "</table></td></tr></table></body></html>"
    )


def _an_https_url(written: str) -> bool:
    parts = urlsplit(written)
    return (
        parts.scheme == "https"
        and bool(parts.hostname)
        and not any(char in written for char in " \"'<>\n")
    )
