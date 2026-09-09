"""Meta's envelope as this door reads it: every message in it, and nothing when there are none."""

from __future__ import annotations

import pytest

from pinecall.whatsapp.inbound import Inbound, Payload, messages_in
from tests.whatsapp.bodies import (
    ANA,
    SOMEBODY_ELSE,
    THE_CLINICS_NUMBER,
    THE_PHONE_NUMBER_ID,
    a_body,
    a_delivery_receipt,
    a_text,
)

pytestmark = pytest.mark.unit

HOLA = "Hola, ¿tienen algo el martes?"


def read(body: dict[str, object]) -> tuple[Inbound, ...]:
    """One body, through the models this door actually validates it with."""
    return messages_in(Payload.model_validate(body))


def test_one_text_arrives_with_the_door_the_person_and_what_they_wrote() -> None:
    (message,) = read(a_body(a_text(HOLA)))
    assert message.number == THE_CLINICS_NUMBER
    assert message.phone_number_id == THE_PHONE_NUMBER_ID
    assert (message.wa_id, message.caller) == (ANA, f"+{ANA}")
    assert message.name == "Ana García"
    assert (message.message_id, message.kind, message.text) == ("wamid.one", "text", HOLA)


def test_two_messages_in_one_body_arrive_in_the_order_meta_sent_them() -> None:
    body = a_body(a_text("primero", id="wamid.one"), a_text("segundo", id="wamid.two"))
    first, second = read(body)
    assert (first.text, second.text) == ("primero", "segundo")


def test_a_delivery_receipt_carries_no_message_and_yields_none() -> None:
    """The same webhook, the same envelope, and nothing in it for this door to answer."""
    assert read(a_delivery_receipt()) == ()


def test_an_image_arrives_as_its_own_kind_with_no_text() -> None:
    picture = {"from": ANA, "id": "wamid.pic", "type": "image", "image": {"id": "media_1"}}
    (message,) = read(a_body(picture))
    assert (message.kind, message.text) == ("image", None)


def test_a_sender_no_contact_row_names_is_still_a_message_and_has_no_name() -> None:
    (message,) = read(a_body(a_text("hola", wa_id=SOMEBODY_ELSE)))
    assert (message.wa_id, message.name) == (SOMEBODY_ELSE, None)


def test_a_body_that_is_not_a_message_envelope_at_all_yields_none() -> None:
    """Meta sends account updates down this URL too: nothing to read is not an error."""
    assert read({"object": "whatsapp_business_account", "entry": []}) == ()
