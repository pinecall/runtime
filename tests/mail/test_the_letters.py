"""The letters: what they say, what they look like, and what they never reach out for."""

from __future__ import annotations

import re

import pytest

from pinecall.mail import (
    Brand,
    Letter,
    a_forgotten_password,
    a_reset,
    a_test_message,
    an_invitation,
    where_the_card_is,
)
from pinecall.mail.brand import ACCENT
from pinecall.mail.layout import LOGO_HEIGHT, WASH, WIDTH
from pinecall.mail.letters import NOBODY_ASKED

pytestmark = pytest.mark.unit

TO = "berna@clinica.uy"
ORG = "Clínica Norte"
LINK = "https://box.example.com/invitations/inv_TOKEN"
DIES = "2026-10-09T11:02:03+00:00"

LETTERS: list[Letter] = [
    an_invitation(TO, ORG, "Ana Vidal", LINK, DIES),
    a_reset(TO, ORG, "Ana Vidal", LINK, DIES),
    a_forgotten_password(TO, ORG, LINK, DIES),
]

# Anything a client would have to fetch: an image, a stylesheet, a font, a pixel that says the
# letter was opened. The ONE URL in these letters is the card the person is meant to press —
# and the one image a letter may ever carry is the logo the OPERATOR of the box gave it.
AN_OUTSIDE_URL = re.compile(r"(?:src|background)\s*=|url\(|@import|<img\b|<link\b", re.IGNORECASE)

A_LOGO = "https://cdn.example.com/brand/mark.png"
THEIRS = Brand(name="Acme Voice", logo_url=A_LOGO, accent="#ff6600")


@pytest.mark.parametrize("letter", LETTERS)
def test_every_letter_names_the_org_and_carries_the_link_in_both_halves(letter: Letter) -> None:
    """A reader that shows no HTML is a reader somebody can still act on."""
    assert letter.to == TO
    assert ORG in letter.subject or ORG in letter.text
    assert LINK in letter.text
    assert f'href="{LINK}"' in letter.html


@pytest.mark.parametrize("letter", LETTERS)
def test_no_letter_fetches_anything_from_anywhere_but_the_operators_own_logo(
    letter: Letter,
) -> None:
    """No image, no webfont, no stylesheet, and above all no pixel that says it was opened.
    The one exception is the logo the operator set, at the address they typed themselves."""
    assert not AN_OUTSIDE_URL.search(letter.html)
    assert letter.html.count("http") == letter.html.count(LINK) == 2
    branded = an_invitation(TO, ORG, "Ana Vidal", LINK, DIES, THEIRS)
    fetched = AN_OUTSIDE_URL.findall(branded.html)
    assert fetched == ["<img", "src="], "the logo, once, and nothing else"
    assert branded.html.count("http") == 3 and branded.html.count(A_LOGO) == 1


def test_the_operators_brand_is_the_name_the_accent_and_the_logo_everywhere_pinecall_was() -> None:
    """A box that is somebody else's product says so in every letter, in both halves."""
    letter = a_reset(TO, ORG, "Ana Vidal", LINK, DIES, THEIRS)
    assert letter.subject == "Reset your Acme Voice password"
    assert "Pinecall" not in letter.text and "Pinecall" not in letter.html
    assert f"Sent by {ORG} through Acme Voice" in letter.text
    assert "background:#ff6600" in letter.html and ACCENT not in letter.html
    assert (
        f'<img src="{A_LOGO}" alt="Acme Voice" height="{LOGO_HEIGHT}" '
        f'style="display:block;height:{LOGO_HEIGHT}px;width:auto;' in letter.html
    )
    assert ">Acme Voice</span>" not in letter.html, "a name is never typeset over the card"
    named = a_reset(TO, ORG, "Ana", LINK, DIES, Brand(name="Acme Voice"))
    assert ">Acme Voice</span>" not in named.html and "<img" not in named.html, (
        "a brand with no logo has no row above the card: the name is in the words and the footer"
    )
    assert "Acme Voice" in named.html


def test_the_test_message_is_the_one_frame_too_and_asks_nothing() -> None:
    letter = a_test_message(TO, THEIRS)
    assert letter.subject == "Acme Voice test message" and letter.to == TO
    assert "took it" in letter.text and "href=" not in letter.html and A_LOGO in letter.html
    assert a_test_message(TO).subject == "Pinecall test message"


@pytest.mark.parametrize("letter", LETTERS)
def test_every_letter_is_the_one_frame_the_brand_draws(letter: Letter) -> None:
    """Table-based and inline-styled, because a letter is read in Outlook as well as anywhere."""
    assert letter.html.startswith("<!DOCTYPE html>")
    assert f"background:{WASH}" in letter.html and f"max-width:{WIDTH}px" in letter.html
    assert f"background:{ACCENT}" in letter.html and "border-radius:9px" in letter.html
    assert ">Pinecall</span>" not in letter.html, "no logo, so nothing is drawn above the card"
    assert 'role="presentation"' in letter.html


@pytest.mark.parametrize("letter", LETTERS)
def test_the_footer_says_who_it_is_from_and_the_day_the_link_dies(letter: Letter) -> None:
    """A letter from a tenant's own mail server still says whose it is, in both halves."""
    footer = f"Sent by {ORG} through Pinecall · this link opens once and dies on 9 October 2026"
    assert footer in letter.text
    assert "Sent by Cl&iacute;nica Norte through Pinecall" in letter.html or footer in letter.html


@pytest.mark.parametrize("letter", LETTERS)
def test_no_letter_carries_a_token_anywhere_but_in_its_link(letter: Letter) -> None:
    """The token is the right this letter hands over, and the link is the whole of the handing."""
    assert letter.text.count("inv_TOKEN") == 1


def test_the_subjects_are_the_two_the_brand_says() -> None:
    assert LETTERS[0].subject == f"You're invited to {ORG} on Pinecall"
    assert LETTERS[1].subject == LETTERS[2].subject == "Reset your Pinecall password"


def test_the_two_a_person_sent_name_that_person_and_the_one_nobody_sent_does_not() -> None:
    """An invitation and an admin's reset were somebody's doing; a forgotten password was not."""
    assert "Ana Vidal" in LETTERS[0].text and "Ana Vidal" in LETTERS[1].text
    assert "Ana Vidal" not in LETTERS[2].text


def test_only_the_letter_nobody_asked_for_says_what_to_do_if_nobody_asked() -> None:
    """The one that can arrive unbidden is the one that has to be harmless to ignore."""
    assert [NOBODY_ASKED in letter.text for letter in LETTERS] == [False, False, True]


@pytest.mark.parametrize(
    "named", ["<script>alert(1)</script>", 'x" onmouseover="alert(1)', "Ana & Co"]
)
def test_a_name_somebody_typed_is_data_in_the_html_half(named: str) -> None:
    """A member's name and an org's are whatever was typed into a form: escaped, both of them."""
    letter = an_invitation(TO, named, named, LINK, DIES)
    assert "<script>" not in letter.html and 'onmouseover="' not in letter.html
    assert named not in letter.html and named in letter.text


def test_a_link_with_a_quote_in_it_cannot_break_out_of_the_href() -> None:
    """The one attribute a letter writes from a value, so it is the one worth pinning."""
    letter = an_invitation(TO, ORG, "Ana", 'https://box.test/x" onclick="a', DIES)
    assert 'onclick="a"' not in letter.html and "&quot;" in letter.html


def test_a_letter_with_no_expiry_says_who_it_is_from_and_stops_there() -> None:
    """Nothing is invented: an org that handed over no date gets a footer without one."""
    assert a_reset(TO, ORG, "Ana", LINK).text.endswith(f"Sent by {ORG} through Pinecall")
    assert a_reset(TO, ORG, "Ana", LINK, "not a date").text.endswith("through Pinecall")


def test_the_link_is_the_console_card_at_the_name_this_gateway_answers_to() -> None:
    """One card takes a password: an invitation and a reset are the same door underneath."""
    assert where_the_card_is("https://box.example.com/", "inv_abc") == (
        "https://box.example.com/invitations/inv_abc"
    )
