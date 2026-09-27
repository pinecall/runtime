"""Mail: which mailbox a letter goes through, what the five letters say, and what SMTP answers."""

import smtplib
from datetime import UTC, datetime

import pytest
from cryptography.fernet import Fernet

from pinecall.domain.errors import DeclarationRefused, UpstreamFailed
from pinecall.postgres.pool import Pool
from pinecall.tenancy.mail import (
    Brand,
    Letter,
    Link,
    Mailbox,
    Outbox,
    apply_brand,
    box_mail_of,
    brand_of,
    card_link,
    drop_box_mail,
    drop_mail,
    forgotten_password_letter,
    invitation_letter,
    mail_of,
    parse_mailbox_url,
    post,
    probe_letter,
    put_box_mail,
    put_brand,
    put_mail,
    reset_letter,
    signup_code_letter,
)
from pinecall.tenancy.orgs import create
from pinecall.tenancy.vault import vault_of
from tests.conftest import postgres
from tests.fakes import MailServer, Postbox

VAULT = vault_of(Fernet.generate_key().decode())
SENDER = "Clínica <no-reply@clinica.test>"
THE_ORGS = Mailbox("smtp.clinica.test", 587, "starttls", "clinica", "their-pass", SENDER)
THE_BOXS = Mailbox("smtp.box.test", 465, "tls", "box", "box-pass", "Box <no-reply@box.test>")
LINK = Link(
    org="Clínica Norte",
    link="https://box.test/invitations/inv_x",
    by="Ana",
    dies=datetime(2026, 10, 9, tzinfo=UTC),
)
LETTER = Letter("bo@clinica.test", "Hola", "the text", "<p>the html</p>")


@pytest.fixture
def postbox(monkeypatch: pytest.MonkeyPatch) -> Postbox:
    kept = Postbox()
    monkeypatch.setattr(MailServer, "postbox", kept)
    monkeypatch.setattr(smtplib, "SMTP", MailServer)
    monkeypatch.setattr(smtplib, "SMTP_SSL", MailServer)
    return kept


def _every_letter(brand: Brand) -> list[Letter]:
    return [
        invitation_letter("bo@clinica.test", LINK, brand),
        reset_letter("bo@clinica.test", LINK, brand),
        forgotten_password_letter("bo@clinica.test", LINK, brand),
    ]


async def test_a_letter_reaches_the_server_with_both_parts_and_the_envelope_it_declares(
    postbox: Postbox,
) -> None:
    await post(THE_ORGS, LETTER)
    (sent,) = postbox.sent
    assert (sent["From"], sent["To"], sent["Subject"]) == (SENDER, "bo@clinica.test", "Hola")
    assert [part.get_content_type() for part in sent.walk()][1:] == ["text/plain", "text/html"]
    assert (postbox.starttls, postbox.logins) == (1, [("clinica", "their-pass")])


async def test_a_relay_that_asks_for_nothing_is_never_signed_in_to(postbox: Postbox) -> None:
    await post(Mailbox("localhost", 25, "none", "", "", SENDER), LETTER)
    assert (postbox.starttls, postbox.logins, len(postbox.sent)) == (0, [], 1)


async def test_a_server_that_refuses_the_password_is_its_own_sentence(postbox: Postbox) -> None:
    postbox.refuses_login = (535, "Authentication credentials invalid")
    with pytest.raises(UpstreamFailed, match="535 Authentication credentials invalid") as refused:
        await post(THE_ORGS, LETTER)
    assert "their-pass" not in str(refused.value)
    assert postbox.sent == []


async def test_a_server_that_refuses_the_letter_says_why_and_the_password_is_not_in_it(
    postbox: Postbox,
) -> None:
    postbox.refuses_letter = (554, "Message rejected")
    with pytest.raises(UpstreamFailed, match="554 Message rejected") as refused:
        await post(THE_ORGS, LETTER)
    assert "their-pass" not in str(refused.value)


async def test_a_server_nobody_is_listening_at_is_a_refusal_and_never_a_crash() -> None:
    nobody = Mailbox("127.0.0.1", 1, "none", "", "", SENDER)
    with pytest.raises(UpstreamFailed, match=r"127\.0\.0\.1:1 did not answer"):
        await post(nobody, LETTER, within_s=2)


async def test_a_header_may_not_carry_a_newline_and_no_socket_is_opened(postbox: Postbox) -> None:
    with pytest.raises(UpstreamFailed, match="could not be written"):
        await post(THE_ORGS, Letter("bo@clinica.test", "Hola\nBcc: x@y.z", "t", "h"))
    assert postbox.hosts == []


def test_a_url_says_the_port_and_the_security_when_it_names_neither() -> None:
    assert parse_mailbox_url("smtp://u:p@mail.test", SENDER).port == 587
    assert parse_mailbox_url("smtps://u:p@mail.test", SENDER).security == "tls"
    assert parse_mailbox_url("smtp://u:p@mail.test:2525", SENDER).port == 2525


def test_an_ses_password_survives_the_url_percent_encoded_or_pasted_raw() -> None:
    raw = parse_mailbox_url("smtp://AKIA:ab/c+d=@email-smtp.test", SENDER)
    encoded = parse_mailbox_url("smtp://AKIA:ab%2Fc%2Bd%3D@email-smtp.test", SENDER)
    assert {raw.password, encoded.password} == {"ab/c+d="}


def test_a_refused_url_never_repeats_the_password_it_carried() -> None:
    with pytest.raises(DeclarationRefused) as refused:
        parse_mailbox_url("smtp://u:secret-pass@", SENDER)
    assert "secret-pass" not in str(refused.value)


def test_something_that_is_not_a_mail_url_is_refused_by_name() -> None:
    with pytest.raises(DeclarationRefused, match="smtp://"):
        parse_mailbox_url("https://mail.test", SENDER)


def test_a_from_with_no_address_in_it_is_refused_before_a_socket_is_opened() -> None:
    with pytest.raises(DeclarationRefused, match="carries no email address"):
        Mailbox("mail.test", 587, "starttls", "u", "p", "Clínica")


def test_every_letter_names_the_org_and_carries_the_link_in_both_halves() -> None:
    for letter in _every_letter(Brand()):
        assert "Clínica Norte" in letter.text
        assert LINK.link in letter.text
        assert LINK.link in letter.html


def test_no_letter_fetches_anything_but_the_operators_own_logo() -> None:
    for letter in [*_every_letter(Brand()), probe_letter("a@b.test", Brand())]:
        assert "<img" not in letter.html
    logo = Brand(logo_url="https://box.test/logo.png")
    assert 'src="https://box.test/logo.png"' in invitation_letter("a@b.test", LINK, logo).html


def test_the_operators_brand_is_the_name_and_the_accent_everywhere_pinecall_was() -> None:
    brand = Brand(name="Acme Voice", accent="#112233")
    letter = invitation_letter("a@b.test", LINK, brand)
    assert "Acme Voice" in letter.subject
    assert "#112233" in letter.html
    assert "Pinecall" not in letter.html


def test_the_footer_says_who_it_is_from_and_the_day_the_link_dies() -> None:
    letter = invitation_letter("a@b.test", LINK, Brand())
    assert (
        "Sent by Clínica Norte through Pinecall · this link opens once and dies on 9 October 2026"
        in letter.text
    )


def test_a_letter_with_no_expiry_says_who_it_is_from_and_stops_there() -> None:
    open_ended = Link(org="Clínica Norte", link=LINK.link, by="Ana")
    assert invitation_letter("a@b.test", open_ended, Brand()).text.endswith(
        "Sent by Clínica Norte through Pinecall"
    )


def test_only_the_letter_nobody_asked_for_says_what_to_do_if_nobody_asked() -> None:
    asked, reset, unbidden = _every_letter(Brand())
    assert "did not ask for this" not in asked.text + reset.text
    assert "did not ask for this" in unbidden.text


def test_the_two_a_person_sent_name_that_person_and_the_one_nobody_sent_does_not() -> None:
    invited, reset, forgotten = _every_letter(Brand())
    assert "Ana" in invited.text
    assert "Ana" in reset.text
    assert "Ana" not in forgotten.text


def test_a_name_somebody_typed_is_data_in_the_html_half() -> None:
    typed = Link(org="<script>x</script>", link=LINK.link, by="<b>Ana</b>")
    html = invitation_letter("a@b.test", typed, Brand()).html
    assert "<script>" not in html
    assert "&lt;b&gt;Ana&lt;/b&gt;" in html


def test_a_link_with_a_quote_in_it_cannot_break_out_of_the_href() -> None:
    quoted = Link(org="C", link='https://box.test/x" onclick="y', by="Ana")
    assert 'onclick="y' not in invitation_letter("a@b.test", quoted, Brand()).html


def test_the_link_is_the_console_card_at_the_name_this_gateway_answers_to() -> None:
    assert card_link("https://box.test/", "inv_1") == "https://box.test/invitations/inv_1"


def test_a_signup_code_is_a_code_in_a_box_with_no_link_and_not_in_the_subject() -> None:
    letter = signup_code_letter("a@b.test", "482913", "Ana", Brand())
    assert "482913" not in letter.subject
    assert "482913" in letter.text
    assert "482913" in letter.html
    assert "href" not in letter.html


def test_the_test_message_is_the_one_frame_too_and_asks_nothing() -> None:
    letter = probe_letter("a@b.test", Brand())
    assert letter.subject == "Pinecall test message"
    assert "<a " not in letter.html


def test_a_brand_refuses_what_it_would_write_into_every_letter() -> None:
    with pytest.raises(DeclarationRefused, match="one line"):
        Brand(name="Acme\nBcc: x")
    with pytest.raises(DeclarationRefused, match="#rrggbb"):
        Brand(accent="red")
    with pytest.raises(DeclarationRefused, match="https://"):
        Brand(logo_url="http://box.test/logo.png")


def test_a_field_left_out_keeps_what_it_had_and_an_empty_one_goes_back_to_the_default() -> None:
    brand = Brand(name="Acme", logo_url="https://a.test/l.png", accent="#112233")
    assert apply_brand(brand, accent="#445566") == Brand("Acme", "https://a.test/l.png", "#445566")
    assert apply_brand(brand, name="", logo_url="") == Brand(accent="#112233")


@postgres
async def test_the_brand_is_read_off_the_box_row_and_a_row_nobody_set_is_pinecall(
    pool: Pool,
) -> None:
    assert await brand_of(pool) == Brand()
    await put_brand(pool, Brand(name="Acme Voice"))
    assert (await brand_of(pool)).name == "Acme Voice"


@postgres
async def test_an_orgs_mailbox_round_trips_and_the_password_is_not_in_the_row(pool: Pool) -> None:
    org = await create(pool, "clinica-norte", "Clínica Norte")
    await put_mail(pool, VAULT, org.id, THE_ORGS)
    standing = await mail_of(pool, VAULT, org.id)
    assert standing is not None
    assert (standing.mailbox, standing.source) == (THE_ORGS, "org")
    async with pool.connection() as connection:
        row = await (await connection.execute("SELECT * FROM org_mail")).fetchone()
    assert row is not None
    assert "their-pass" not in str(dict(row))
    assert await drop_mail(pool, org.id)
    assert not await drop_mail(pool, org.id)


@postgres
async def test_the_boxs_stored_mailbox_wins_over_the_environments(pool: Pool) -> None:
    environment = parse_mailbox_url("smtp://env:env-pass@smtp.env.test", SENDER)
    found = await box_mail_of(pool, VAULT, environment)
    assert found is not None
    assert found.source == "environment"
    await put_box_mail(pool, VAULT, THE_BOXS)
    stored = await box_mail_of(pool, VAULT, environment)
    assert stored is not None
    assert (stored.mailbox, stored.source) == (THE_BOXS, "stored")
    await drop_box_mail(pool)
    assert await box_mail_of(pool, VAULT, None) is None


@postgres
async def test_a_letter_goes_through_the_orgs_mailbox_before_the_boxs(
    pool: Pool, postbox: Postbox
) -> None:
    org = await create(pool, "clinica-norte", "Clínica Norte")
    outbox = Outbox(pool, VAULT, THE_BOXS)
    assert await outbox.sent(org.id, LETTER) is None
    await put_mail(pool, VAULT, org.id, THE_ORGS)
    assert await outbox.sent(org.id, LETTER) is None
    assert [host for host, _ in postbox.hosts] == ["smtp.box.test", "smtp.clinica.test"]


@postgres
async def test_how_a_letter_went_is_kept_on_the_mailbox_it_went_through(
    pool: Pool, postbox: Postbox
) -> None:
    org = await create(pool, "clinica-norte", "Clínica Norte")
    await put_mail(pool, VAULT, org.id, THE_ORGS)
    outbox = Outbox(pool, VAULT, None)
    postbox.refuses_letter = (554, "Message rejected")
    assert (
        await outbox.sent(org.id, LETTER)
        == "smtp.clinica.test:587 refused it: 554 Message rejected"
    )
    refused = await mail_of(pool, VAULT, org.id)
    assert refused is not None
    assert (refused.last_error, refused.verified_at) == (
        "smtp.clinica.test:587 refused it: 554 Message rejected",
        None,
    )
    postbox.refuses_letter = None
    await outbox.sent(org.id, LETTER)
    went = await mail_of(pool, VAULT, org.id)
    assert went is not None
    assert (went.last_error, went.verified_at is not None) == (None, True)


@postgres
async def test_a_letter_queued_is_sent_in_the_background_and_none_is_queued_with_no_mailbox(
    pool: Pool, postbox: Postbox
) -> None:
    assert not await Outbox(pool, VAULT, None).post(None, LETTER)
    outbox = Outbox(pool, VAULT, THE_BOXS)
    assert await outbox.post(None, LETTER)
    await outbox.drained()
    assert len(postbox.sent) == 1
