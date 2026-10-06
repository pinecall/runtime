"""Tests for the letters the gateway sends, worded and framed in the org's brand."""

from dataclasses import replace

import pytest

from pinecall.domain.errors import DeclarationRefused, UpstreamFailed
from pinecall.postgres.pool import Pool
from pinecall.process.connections import Connections
from pinecall.tenancy.letters import (
    Brand,
    Link,
    account_kept_letter,
    brand_of,
    card_link,
    invitation_letter,
    invitation_waiting_letter,
    probe_letter,
    put_brand,
    signup_code_letter,
)
from pinecall.tenancy.mail import (
    Outbox,
    mail_of,
    post,
    put_mail,
)
from pinecall.tenancy.orgs import create
from tests.conftest import postgres
from tests.fakes.mail import Postbox
from tests.tenancy.test_mail import LETTER, LINK, SENDER, THE_BOXS, THE_ORGS, VAULT, every_letter


async def test_a_letter_reaches_the_server_with_both_parts_and_the_envelope_it_declares(
    postbox: Postbox,
) -> None:
    await post(THE_ORGS, LETTER)
    (sent,) = postbox.sent
    assert (sent["From"], sent["To"], sent["Subject"]) == (SENDER, "bo@clinica.test", "Hola")
    assert [part.get_content_type() for part in sent.walk()][1:] == ["text/plain", "text/html"]
    assert (postbox.starttls, postbox.logins) == (1, [("clinica", "their-pass")])


async def test_a_server_that_refuses_the_letter_says_why_and_the_password_is_not_in_it(
    postbox: Postbox,
) -> None:
    postbox.refuses_letter = (554, "Message rejected")
    with pytest.raises(UpstreamFailed, match="554 Message rejected") as refused:
        await post(THE_ORGS, LETTER)
    assert "their-pass" not in str(refused.value)


def test_every_letter_names_the_org_and_carries_the_link_in_both_halves() -> None:
    for letter in every_letter(Brand()):
        assert "Clínica Norte" in letter.text
        assert LINK.link in letter.text
        assert LINK.link in letter.html


def test_no_letter_fetches_anything_but_the_operators_own_logo() -> None:
    for letter in [*every_letter(Brand()), probe_letter("a@b.test", Brand())]:
        assert "<img" not in letter.html
    logo = Brand(logo_url="https://box.test/logo.png")
    assert 'src="https://box.test/logo.png"' in invitation_letter("a@b.test", LINK, logo).html


# A sign-up answers every address alike; these say the why to the address's owner, with no link.
def test_a_sign_up_for_a_kept_address_tells_its_owner_and_carries_no_link_or_code() -> None:
    kept = account_kept_letter("ana@b.test", Brand(name="Acme Voice"))
    waiting = invitation_waiting_letter("ana@b.test", Brand())
    assert (kept.to, kept.subject) == ("ana@b.test", "You already have an account on Acme Voice")
    assert "the password you have" in kept.text
    assert "Accept that invitation first" in waiting.text
    for letter in (kept, waiting):
        assert "/invitations/" not in letter.html
        assert "ignore this email" in letter.text


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
    params, reset, unbidden = every_letter(Brand())
    assert "did not ask for this" not in params.text + reset.text
    assert "did not ask for this" in unbidden.text


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


def test_a_brand_refuses_what_it_would_write_into_every_letter() -> None:
    with pytest.raises(DeclarationRefused, match="one line"):
        Brand(name="Acme\nBcc: x")
    with pytest.raises(DeclarationRefused, match="#rrggbb"):
        Brand(accent="red")
    with pytest.raises(DeclarationRefused, match="https://"):
        Brand(logo_url="http://box.test/logo.png")


@postgres
async def test_the_brand_is_read_off_the_box_row_and_a_row_nobody_set_is_pinecall(
    pool: Pool,
) -> None:
    assert await brand_of(pool) == Brand()
    await put_brand(pool, Brand(name="Acme Voice"))
    assert (await brand_of(pool)).name == "Acme Voice"


@postgres
async def test_a_letter_goes_through_the_orgs_mailbox_before_the_boxs(
    pool: Pool, postbox: Postbox, connections: Connections
) -> None:
    org = await create(pool, "clinica-norte", "Clínica Norte")
    outbox = Outbox(replace(connections, vault=VAULT), THE_BOXS)
    assert await outbox.sent(org.id, LETTER) is None
    await put_mail(pool, VAULT, org.id, THE_ORGS)
    assert await outbox.sent(org.id, LETTER) is None
    assert [host for host, _ in postbox.hosts] == ["smtp.box.test", "smtp.clinica.test"]


@postgres
async def test_how_a_letter_went_is_kept_on_the_mailbox_it_went_through(
    pool: Pool, postbox: Postbox, connections: Connections
) -> None:
    org = await create(pool, "clinica-norte", "Clínica Norte")
    await put_mail(pool, VAULT, org.id, THE_ORGS)
    outbox = Outbox(replace(connections, vault=VAULT), None)
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
    postbox: Postbox, connections: Connections
) -> None:
    assert not await Outbox(replace(connections, vault=VAULT), None).post(None, LETTER)
    outbox = Outbox(replace(connections, vault=VAULT), THE_BOXS)
    assert await outbox.post(None, LETTER)
    await outbox.drained()
    assert len(postbox.sent) == 1
