"""Tests for the events: the sockets reads the golden log, and an event keeps its wire keys."""

import pytest

from pinecall.domain.errors import DeclarationRefused
from pinecall.wire.events import EPHEMERAL_EVENTS, EVENTS, TERMINAL_EVENT, CallStarted, event_of
from pinecall.wire.frames import Entry
from tests.wire.golden import golden_entries


def test_the_ephemeral_events_and_the_terminal_one_are_registered() -> None:
    assert set(EVENTS) >= EPHEMERAL_EVENTS
    assert TERMINAL_EVENT in EVENTS


def test_every_entry_of_the_golden_log_is_the_model_its_type_names() -> None:
    entries = golden_entries()
    for entry in entries:
        assert type(event_of(entry)) is EVENTS[entry.type]
    assert [entry.type for entry in entries if not entry.ephemeral][-1] == TERMINAL_EVENT


def test_an_entry_of_a_type_nobody_declared_is_refused_with_its_name() -> None:
    entry = Entry(
        seq=1, ts=1.0, call="c", agent="a", type="call.imagined", ephemeral=False, data={}
    )
    with pytest.raises(DeclarationRefused, match=r"unknown event type: call\.imagined"):
        event_of(entry)


def test_an_event_goes_on_the_wire_by_its_wire_keys_and_absent_fields_stay_absent() -> None:
    started = CallStarted.read(
        {
            "channel": "phone",
            "direction": "inbound",
            "from": "+1",
            "to": "+2",
            "caller": None,
            "started_at": 1.0,
        },
        "call.started",
    )
    assert started.written()["from"] == "+1"
    assert "run" not in started.written()
