"""Route: a door into an agent. A number is a route, never an agent."""

import re
from typing import Any

import pytest

from pinecall.types import DeclarationRefused, Route

pytestmark = pytest.mark.unit


def test_a_route_with_no_door_is_refused() -> None:
    row: dict[str, Any] = {"org": "clinics", "agent": "clinica-norte", "channel": ""}
    with pytest.raises(DeclarationRefused, match="a route is a door"):
        Route(**row)


def test_a_phone_route_answers_at_a_number_in_e164() -> None:
    main_line = Route("clinics", "clinica-norte", "phone", number="+34910000001")
    assert main_line.door == ("phone", "+34910000001")
    for number in (None, "910000001", "+34 910 000 001", "+0", "+" + "1" * 16):
        with pytest.raises(DeclarationRefused, match=re.escape("E.164")):
            Route("clinics", "clinica-norte", "phone", number=number)


def test_a_whatsapp_route_needs_a_number_too() -> None:
    with pytest.raises(DeclarationRefused, match="whatsapp route answers at a number"):
        Route("clinics", "clinica-norte", "whatsapp")


def test_the_web_widget_answers_at_no_number() -> None:
    assert Route("clinics", "clinica-norte", "web").door == ("web", None)
    with pytest.raises(DeclarationRefused, match="no number"):
        Route("clinics", "clinica-norte", "web", number="+34910000001")


def test_a_route_names_its_fleet_and_its_agent() -> None:
    for org, agent in (("", "clinica-norte"), ("clinics", "")):
        with pytest.raises(DeclarationRefused, match="org"):
            Route(org, agent, "web")


def test_the_same_door_is_the_same_door_whatever_the_fleet_or_the_label() -> None:
    by_day = Route("clinics", "clinica-norte", "phone", "+34910000001", label="main line")
    by_night = Route("night", "guardia", "phone", "+34910000001", label="after hours")
    assert by_day.door == by_night.door
    assert by_day != by_night
