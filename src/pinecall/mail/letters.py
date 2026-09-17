"""The three letters this gateway sends, each in plain text and the same words in the frame."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pinecall.mail.layout import a_letter, button, fallback, heading, paragraph, small

# Where the console's card that takes a password lives. An invitation and a reset are the same
# door underneath (`POST /v1/invitations/{token}`), so they are the same link — which is why a
# person who was sent both opens either and lands on one card.
CARD = "/invitations/{token}"

OPEN_IT = "Open it in a browser"

# What every letter says under the button, and what the one nobody asked for says as well.
ONCE = "This link opens once."
NOBODY_ASKED = "If you did not ask for this, nothing has changed and you can ignore this message."

# The footer, the same shape on all three: who it is really from, and when the link stops working.
FROM_THEM = "Sent by {org} through Pinecall"
DIES_ON = "{sent} · this link opens once and dies on {date}"

MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


@dataclass(frozen=True)
class Letter:
    """One message, ready to post: who it is to, what it says, and the same words marked up."""

    to: str
    subject: str
    text: str
    html: str


def an_invitation(to: str, org: str, inviter: str, link: str, dies: str | None = None) -> Letter:
    """Somebody was invited to an org: the accept link, and who invited them."""
    return _a_letter(
        to,
        f"You're invited to {org} on Pinecall",
        "Join your team",
        [
            f"{inviter} has invited you to join {org} on Pinecall.",
            "Choose a password and you are in.",
        ],
        "Accept the invitation",
        link,
        org,
        dies,
    )


def a_reset(to: str, org: str, admin: str, link: str, dies: str | None = None) -> Letter:
    """An admin of the org handed this member's password back: the same card, a different why."""
    return _a_letter(
        to,
        "Reset your Pinecall password",
        "Set a new password",
        [f"{admin} has reset your password for {org}.", "Choose a new one to sign in again."],
        "Choose a password",
        link,
        org,
        dies,
    )


def a_forgotten_password(to: str, org: str, link: str, dies: str | None = None) -> Letter:
    """Somebody asked for it themselves, so it says so, and says what to do if they did not."""
    return _a_letter(
        to,
        "Reset your Pinecall password",
        "Reset your password",
        [f"Somebody asked to reset the password for this address at {org}."],
        "Choose a password",
        link,
        org,
        dies,
        unbidden=True,
    )


def where_the_card_is(base: str, token: str) -> str:
    """The link a letter carries: the console's own card, at the name this gateway is reached by."""
    return f"{base.rstrip('/')}{CARD.format(token=token)}"


def _on_the(moment: str | None) -> str:
    """A stored instant as a letter says a date: `9 October 2026`, or nothing at all."""
    if not moment:
        return ""
    try:
        day = datetime.fromisoformat(moment)
    except ValueError:
        return ""
    return f"{day.day} {MONTHS[day.month - 1]} {day.year}"


# The HTML half and the text half are written from the same paragraphs, so neither can say
# something the other does not: a letter whose two parts disagree is a letter a person reads twice.
# The link is written out in the text under the button for a reader that shows no colour at all.
def _a_letter(
    to: str,
    subject: str,
    title: str,
    paragraphs: list[str],
    label: str,
    link: str,
    org: str,
    dies: str | None,
    *,
    unbidden: bool = False,
) -> Letter:
    """One letter, twice: the framed card, and the plain words somebody can act on either way."""
    quiet = [ONCE] + ([NOBODY_ASKED] if unbidden else [])
    marked = (
        heading(title)
        + "".join(paragraph(said) for said in paragraphs)
        + button(label, link)
        + fallback(link)
        + "".join(small(said) for said in quiet)
    )
    written = "\n\n".join([title, *paragraphs, f"{OPEN_IT}:\n\n{link}", *quiet, _footer(org, dies)])
    return Letter(
        to=to,
        subject=subject,
        text=written,
        html=a_letter(paragraphs[0], marked, _footer(org, dies)),
    )


def _footer(org: str, dies: str | None) -> str:
    """Who it is really from, and the day the link stops working when one is known."""
    sent = FROM_THEM.format(org=org)
    date = _on_the(dies)
    return DIES_ON.format(sent=sent, date=date) if date else sent
