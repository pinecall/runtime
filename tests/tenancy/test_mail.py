"""Mail: which mailbox a letter goes through, what the five letters say, and what SMTP answers."""

from datetime import UTC, datetime

import pytest
from cryptography.fernet import Fernet

from pinecall.domain.errors import DeclarationRefused, UpstreamFailed
from pinecall.postgres.pool import Pool
from pinecall.process.connections import vault_of
from pinecall.tenancy._letters import (
    Link,
    forgotten_password_letter,
    invitation_letter,
    probe_letter,
    reset_letter,
)
from pinecall.tenancy._mail import (
    Brand,
    Letter,
    Mailbox,
    apply_brand,
    box_mail_of,
    drop_box_mail,
    drop_mail,
    mail_of,
    parse_mailbox_url,
    post,
    put_box_mail,
    put_mail,
)
from pinecall.tenancy.orgs import create
from tests.conftest import postgres
from tests.fakes.mail import Postbox

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


def every_letter(brand: Brand) -> list[Letter]:
    return [
        invitation_letter("bo@clinica.test", LINK, brand),
        reset_letter("bo@clinica.test", LINK, brand),
        forgotten_password_letter("bo@clinica.test", LINK, brand),
    ]


async def test_a_relay_that_asks_for_nothing_is_never_signed_in_to(postbox: Postbox) -> None:
    await post(Mailbox("localhost", 25, "none", "", "", SENDER), LETTER)
    assert (postbox.starttls, postbox.logins, len(postbox.sent)) == (0, [], 1)


async def test_a_server_that_refuses_the_password_is_its_own_sentence(postbox: Postbox) -> None:
    postbox.refuses_login = (535, "Authentication credentials invalid")
    with pytest.raises(UpstreamFailed, match="535 Authentication credentials invalid") as refused:
        await post(THE_ORGS, LETTER)
    assert "their-pass" not in str(refused.value)
    assert postbox.sent == []


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


def test_the_two_a_person_sent_name_that_person_and_the_one_nobody_sent_does_not() -> None:
    invited, reset, forgotten = every_letter(Brand())
    assert "Ana" in invited.text
    assert "Ana" in reset.text
    assert "Ana" not in forgotten.text


def test_a_name_somebody_typed_is_data_in_the_html_half() -> None:
    typed = Link(org="<script>x</script>", link=LINK.link, by="<b>Ana</b>")
    html = invitation_letter("a@b.test", typed, Brand()).html
    assert "<script>" not in html
    assert "&lt;b&gt;Ana&lt;/b&gt;" in html


def test_the_test_message_is_the_one_frame_too_and_asks_nothing() -> None:
    letter = probe_letter("a@b.test", Brand())
    assert letter.subject == "Pinecall test message"
    assert "<a " not in letter.html


def test_a_field_left_out_keeps_what_it_had_and_an_empty_one_goes_back_to_the_default() -> None:
    brand = Brand(name="Acme", logo_url="https://a.test/l.png", accent="#112233")
    assert apply_brand(brand, accent="#445566") == Brand("Acme", "https://a.test/l.png", "#445566")
    assert apply_brand(brand, name="", logo_url="") == Brand(accent="#112233")


@postgres
async def test_an_orgs_mailbox_round_trips_and_the_password_is_not_in_the_row(pool: Pool) -> None:
    org = await create(pool, "clinica-norte", "Clínica Norte")
    await put_mail(pool, VAULT, org.id, THE_ORGS)
    existing = await mail_of(pool, VAULT, org.id)
    assert existing is not None
    assert (existing.mailbox, existing.source) == (THE_ORGS, "org")
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
