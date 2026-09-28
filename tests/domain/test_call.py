"""Tests for what a call is and the day it runs in."""

import re
from dataclasses import fields
from datetime import date

import pytest

from pinecall.domain.call import A_CALL, Contact, Route, new_call_id, parse_zone, today_in
from pinecall.domain.errors import DeclarationRefused
from pinecall.wire import parts as wire
from tests.domain.conftest import (
    MAIN_LINE,
    WIDGET,
    phone_call,
)


def test_a_resolved_contact_id_outranks_the_number() -> None:
    assert phone_call(contact=Contact(id="P-2231", phone="+34600000001")).remembered_as == "P-2231"


def test_a_web_visitor_with_no_id_is_nobody_to_memory() -> None:
    visitor = phone_call(channel="web", route=WIDGET, caller="visitor_1", contact=None)
    assert visitor.remembered_as is None
    named = phone_call(channel="web", route=WIDGET, caller="visitor_1", contact=Contact(name="Ana"))
    assert named.remembered_as is None


def test_a_call_id_minted_here_is_the_prefix_and_32_hex_digits() -> None:
    minted = new_call_id()
    assert minted.startswith(A_CALL)
    assert re.fullmatch(r"[0-9a-f]{32}", minted.removeprefix(A_CALL))
    assert minted != new_call_id()


def test_a_phone_route_answers_at_a_number_in_e164() -> None:
    assert MAIN_LINE.door == ("phone", "+34910000001")
    for number in (None, "910000001", "+34 910 000 001", "+0", "+" + "1" * 16):
        with pytest.raises(DeclarationRefused, match=re.escape("E.164")):
            Route("clinics", "clinica-norte", "phone", number=number)


def test_a_whatsapp_route_needs_a_number_too() -> None:
    with pytest.raises(DeclarationRefused, match="whatsapp route answers at a number"):
        Route("clinics", "clinica-norte", "whatsapp")


def test_the_web_widget_answers_at_no_number() -> None:
    assert WIDGET.door == ("web", None)
    with pytest.raises(DeclarationRefused, match="no number"):
        Route("clinics", "clinica-norte", "web", number="+34910000001")


def test_a_route_names_its_org_and_its_agent() -> None:
    for org, agent in (("", "clinica-norte"), ("clinics", "")):
        with pytest.raises(DeclarationRefused, match="org"):
            Route(org, agent, "web")


def test_the_same_door_is_the_same_door_whatever_the_org_or_the_label() -> None:
    by_day = Route("clinics", "clinica-norte", "phone", "+34910000001", label="main line")
    by_night = Route("night", "guardia", "phone", "+34910000001", label="after hours")
    assert by_day.door == by_night.door
    assert by_day != by_night


def test_a_zone_is_an_iana_name_and_today_is_read_in_it() -> None:
    assert parse_zone("Europe/Madrid").key == "Europe/Madrid"
    assert isinstance(today_in("America/Montevideo"), date)
    with pytest.raises(DeclarationRefused, match="not an IANA time zone"):
        parse_zone("Mars/Olympus")


def test_the_contact_is_the_same_shape_on_both_sides() -> None:
    assert {declared.name for declared in fields(Contact)} == set(wire.Contact.model_fields)
