"""CallContext: what is true of a call before the first word, and cannot change after."""

from dataclasses import FrozenInstanceError
from datetime import date
from typing import Any

import pytest

from pinecall.types import CallContext, Contact, DeclarationRefused, Route

pytestmark = pytest.mark.unit

MAIN_LINE = Route("clinics", "clinica-norte", "phone", "+34910000001")
WIDGET = Route("clinics", "clinica-norte", "web")
TODAY = date(2026, 9, 6)


def a_call(**given: Any) -> CallContext:
    """An inbound phone call from a known patient, with whatever the test changes laid on top."""
    inbound: dict[str, Any] = {
        "call": "CA_8f4a",
        "channel": "phone",
        "direction": "inbound",
        "caller": "+34600000001",
        "route": MAIN_LINE,
        "today": TODAY,
        "contact": Contact(phone="+34600000001", name="Ana"),
        **given,
    }
    return CallContext(**inbound)


def test_a_call_comes_through_a_door_of_its_own_channel() -> None:
    assert a_call().route is MAIN_LINE
    with pytest.raises(DeclarationRefused, match="web call cannot come through a phone route"):
        a_call(channel="web", route=MAIN_LINE)


def test_a_call_knows_the_day_it_happens_on() -> None:
    assert a_call().today == TODAY


def test_a_web_visitor_may_be_nobody_yet() -> None:
    visit = a_call(channel="web", route=WIDGET, caller="visitor_8f4a", contact=None)
    assert visit.contact is None
    assert not Contact().is_known
    assert Contact(phone="+34600000001").is_known


def test_the_metadata_is_the_apps_and_defaults_to_nothing() -> None:
    assert a_call().metadata == {}
    assert a_call(metadata={"campaign": "otoño"}).metadata == {"campaign": "otoño"}


def test_a_call_names_itself_its_caller_and_its_direction() -> None:
    broken: list[tuple[dict[str, Any], str]] = [
        ({"call": ""}, "names its call"),
        ({"caller": ""}, "calling side"),
        ({"direction": "sideways"}, "inbound"),
        ({"channel": "sms", "route": WIDGET}, "comes through one of"),
    ]
    for given, rule in broken:
        with pytest.raises(DeclarationRefused, match=rule):
            a_call(**given)


def test_the_context_is_frozen_because_what_changes_is_in_the_log() -> None:
    any_field: str = "caller"
    with pytest.raises(FrozenInstanceError):
        setattr(a_call(), any_field, "+34600000002")
