"""The transport, against a real SMTP conversation: what goes on the wire, and what a no says."""

from __future__ import annotations

import pytest

from pinecall.mail import Letter, MailRefused, post
from pinecall.mail.smtp import build_email
from pinecall.types import DeclarationRefused, Mailbox, parse_mailbox_url
from pinecall_testkit.fake_smtp import BAD_CREDENTIALS, NOT_AUTHORIZED, FakeSmtp

pytestmark = pytest.mark.unit

A_LETTER = Letter(
    to="berna@clinica.uy",
    subject="Ana invited you to Clínica Norte",
    text="Open this link:\n\nhttps://box.test/invitations/inv_abc",
    html='<p>Open this link:<br><a href="https://box.test/invitations/inv_abc">…</a></p>',
)


async def test_a_letter_reaches_the_server_with_both_parts_and_the_envelope_it_declares() -> None:
    """The whole path, over a socket: MAIL FROM, RCPT TO, the headers and both bodies."""
    async with FakeSmtp() as server:
        await post(server.mailbox(), A_LETTER)
    assert len(server.took) == 1
    envelope = server.took[0]
    # The envelope carries the BARE address, and the header carries the name beside it.
    assert envelope.sender == "no-reply@box.test"
    assert envelope.recipients == ("berna@clinica.uy",)
    assert envelope.message["From"] == "Pinecall <no-reply@box.test>"
    assert envelope.message["Subject"] == A_LETTER.subject
    assert envelope.parts["text/plain"].strip() == A_LETTER.text
    assert envelope.parts["text/html"].strip() == A_LETTER.html


async def test_the_link_is_in_the_text_part_so_neither_half_is_the_only_way_in() -> None:
    """A reader that shows no HTML shows a letter somebody can still act on."""
    async with FakeSmtp() as server:
        await post(server.mailbox(), A_LETTER)
    assert "https://box.test/invitations/inv_abc" in server.took[0].parts["text/plain"]


async def test_a_server_that_refuses_the_password_is_its_own_sentence_and_nothing_is_taken() -> (
    None
):
    """What an operator reads when a relay password was rotated and never brought here."""
    async with FakeSmtp(refuses_the_password=True) as server:
        with pytest.raises(MailRefused) as refused:
            await post(server.mailbox(), A_LETTER)
    assert BAD_CREDENTIALS in str(refused.value)
    assert str(server.port) in str(refused.value) and "s3cret" not in str(refused.value)
    assert server.took == []


async def test_a_server_that_refuses_the_letter_says_why_and_the_password_is_not_in_it() -> None:
    """SES's own refusal for an unverified sender, carried whole and with no credential in it."""
    async with FakeSmtp(refuses_the_letter=True) as server:
        with pytest.raises(MailRefused) as refused:
            await post(server.mailbox(), A_LETTER)
    assert NOT_AUTHORIZED in str(refused.value) and "s3cret" not in str(refused.value)


async def test_a_server_nobody_is_listening_at_is_a_refusal_and_never_a_crash() -> None:
    """A relay that is down must read the same way as one that said no: recorded, not raised."""
    async with FakeSmtp() as server:
        shut = server.mailbox()
    with pytest.raises(MailRefused) as refused:
        await post(shut, A_LETTER)
    assert "did not answer" in str(refused.value)


async def test_a_relay_that_asks_for_nothing_is_never_signed_in_to() -> None:
    """A mail server on the same machine takes a letter with no AUTH at all."""
    async with FakeSmtp() as server:
        await post(server.mailbox(user=""), A_LETTER)
    assert len(server.took) == 1


# ── the URL a box declares its mail with ────────────────────────────────────────


def test_a_url_says_the_port_and_the_security_when_it_names_neither() -> None:
    """smtp:// is STARTTLS on 587 and smtps:// implicit TLS on 465, because that is the world."""
    starttls = parse_mailbox_url("smtp://AKIA:pw@email-smtp.eu-west-1.amazonaws.com", "a@b.co")
    assert (starttls.port, starttls.security) == (587, "starttls")
    implicit = parse_mailbox_url("smtps://AKIA:pw@email-smtp.eu-west-1.amazonaws.com", "a@b.co")
    assert (implicit.port, implicit.security) == (465, "tls")
    named = parse_mailbox_url("smtp://relay.test:2525", "a@b.co")
    assert (named.port, named.username, named.password) == (2525, "", "")


# The shape of a real one — base64, with `/` and `+` in it — and never a real one.
AN_SES_SHAPED_PASSWORD = "BAbc/de+FGhi/jk+LmNoPqRsTuVwXyZ0123456789ab"


def test_an_ses_password_survives_the_url_percent_encoded_or_pasted_raw() -> None:
    """A base64 SMTP password carries `/` and `+`: a raw `/` would end a URL's authority."""
    encoded = "BAbc%2Fde%2BFGhi%2Fjk%2BLmNoPqRsTuVwXyZ0123456789ab"
    for written in (encoded, AN_SES_SHAPED_PASSWORD):
        kept = parse_mailbox_url(
            f"smtp://AKIAEXAMPLE:{written}@email-smtp.us-east-1.amazonaws.com:587", "a@b.co"
        )
        assert (kept.username, kept.password) == ("AKIAEXAMPLE", AN_SES_SHAPED_PASSWORD)
        assert (kept.host, kept.port) == ("email-smtp.us-east-1.amazonaws.com", 587)


def test_a_refused_url_never_repeats_the_password_it_carried() -> None:
    """The box logs a refusal at startup, and a log line is the last place a password belongs."""
    for url in (
        f"http://u:{AN_SES_SHAPED_PASSWORD}@relay.test",
        f"smtp://u:{AN_SES_SHAPED_PASSWORD}@",
    ):
        with pytest.raises(DeclarationRefused) as refused:
            parse_mailbox_url(url, "a@b.co")
        assert AN_SES_SHAPED_PASSWORD not in str(refused.value)


@pytest.mark.parametrize(
    "said",
    ["https://relay.test", "smtp://", "relay.test:587", ""],
)
def test_something_that_is_not_a_mail_url_is_refused_by_name(said: str) -> None:
    """A box told to post mail through a web address sends none and says which line to fix."""
    with pytest.raises(DeclarationRefused):
        parse_mailbox_url(said, "a@b.co")


def test_a_from_with_no_address_in_it_is_refused_before_a_socket_is_opened() -> None:
    with pytest.raises(DeclarationRefused):
        Mailbox(
            host="relay.test",
            port=587,
            security="starttls",
            username="",
            password="",
            sender="Pinecall",
        )


def test_a_header_may_not_carry_a_newline() -> None:
    """Two headers out of one field is how a From line becomes somebody else's From line."""
    with pytest.raises(DeclarationRefused):
        Mailbox(
            host="relay.test",
            port=587,
            security="starttls",
            username="",
            password="",
            sender="a@b.co\nBcc: everybody@b.co",
        )


def test_the_message_is_multipart_with_the_text_first() -> None:
    """A reader that shows one part shows the one it can, and the text part is written for that."""
    built = build_email(
        Mailbox(
            host="relay.test",
            port=587,
            security="starttls",
            username="",
            password="",
            sender="a@b.co",
        ),
        A_LETTER,
    )
    assert built.is_multipart()
    assert [part.get_content_type() for part in built.walk()][1:] == ["text/plain", "text/html"]


async def test_a_letter_whose_header_cannot_be_written_is_a_refusal_and_no_socket_is_opened() -> (
    None
):
    """An org's name with a line break in it would be two headers: it is refused, not sent."""
    async with FakeSmtp() as server:
        with pytest.raises(MailRefused) as refused:
            await post(
                server.mailbox(),
                Letter(to="a@b.co", subject="x\nBcc: all@b.co", text="t", html="h"),
            )
    assert "could not be written" in str(refused.value) and server.took == []
