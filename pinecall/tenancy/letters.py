"""The letters the gateway sends, and the brand they are worded and framed in."""

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from html import escape
from urllib.parse import urlsplit

from pinecall.domain.errors import DeclarationRefused
from pinecall.domain.names import JsonObject
from pinecall.postgres.pool import Pool
from pinecall.process import box_settings

logger = logging.getLogger(__name__)

NAME = "Pinecall"

ACCENT = "#5b3df5"

# Written into a style attribute, so six hex digits and nothing else.
A_BRAND_COLOUR = re.compile(r"^#[0-9a-fA-F]{6}$")

# The name goes into a subject line, where a line break would start another header.
LONGEST_NAME = 60

NOT_A_COLOUR = "{said!r} is not an accent: a colour is #rrggbb, six hex digits"

NOT_A_LOGO = (
    "{said!r} is not a logo: an https:// URL of an image, since a mail client fetches it from "
    "wherever the reader is and blocks plain http"
)

BRAND = "brand"

NOBODY_ASKED = "If you did not ask for this, nothing has changed and you can ignore this message."

CARD = "/invitations/{token}"

ONCE = "This link opens once."

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
class Letter:
    """A letter ready to go: to whom, its subject, and the same words as text and as HTML."""

    to: str
    subject: str
    text: str
    html: str


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


NOT_A_NAME = f"a brand's name is one line of at most {LONGEST_NAME} characters"


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
    text = (
        f"This is a test message from {brand.name}. Your mail server took it, so the letters "
        "this gateway sends will reach you."
    )
    body = _heading("It works") + _paragraph(text)
    return Letter(to, f"{brand.name} test message", text, _framed(text, body, brand.name, brand))


# The code rides the preheader, never the subject: the outbox logs subjects, and a logged code is
# a code anybody reading the log could spend.
def signup_code_letter(to: str, code: str, person: str, brand: Brand) -> Letter:
    """The letter that carries a sign-up's six digits."""
    params = f"Hi {person}, enter this code to finish setting up your {brand.name} account:"
    dies = "This code expires in 15 minutes."
    unbidden = f"If you did not create a {brand.name} account, ignore this email."
    footer = f"Sent by {brand.name}"
    body = (
        _heading("Confirm your email")
        + _paragraph(params)
        + _code(code)
        + _small(dies)
        + _small(unbidden)
    )
    text = f"Confirm your email\n\n{params}\n\n{code}\n\n{dies}\n\n{unbidden}\n\n{footer}"
    preheader = f"Your {brand.name} verification code is {code}"
    return Letter(
        to, f"Confirm your {brand.name} email", text, _framed(preheader, body, footer, brand)
    )


async def brand_of(pool: Pool) -> Brand:
    """The box's brand; Pinecall's when nobody set one, or when the row does not read."""
    async with pool.connection() as connection:
        value = await box_settings.read(connection, BRAND)
    if value is None:
        return Brand()
    logo = value.get("logo_url")
    try:
        return Brand(
            name=str(value.get("name") or NAME),
            logo_url=str(logo) if logo else None,
            accent=str(value.get("accent") or ACCENT),
        )
    except DeclarationRefused:
        logger.warning("the box's brand does not read: letters go out as Pinecall's")
        return Brand()


async def put_brand(pool: Pool, brand: Brand) -> None:
    """Keep the box's brand."""
    value: JsonObject = {"name": brand.name, "logo_url": brand.logo_url, "accent": brand.accent}
    async with pool.connection() as connection:
        await box_settings.write(connection, BRAND, value)


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


# Text and HTML come from the same sentences, so the two halves never disagree.
def _linked(to: str, link: Link, brand: Brand, wording: Wording) -> Letter:
    quiet = [ONCE, *([NOBODY_ASKED] if wording.unbidden else [])]
    body = (
        _heading(wording.title)
        + "".join(_paragraph(item) for item in wording.said)
        + _button(wording.label, link.link, brand.accent)
        + _fallback(link.link)
        + "".join(_small(item) for item in quiet)
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
    """Whether the text is an https URL with a host and nothing an attribute would break on."""
    parts = urlsplit(written)
    return (
        parts.scheme == "https"
        and bool(parts.hostname)
        and not any(char in written for char in " \"'<>\n")
    )
