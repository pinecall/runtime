"""Tests for the events: every one is the generated one, and the registry reads the golden log."""

import pytest

from pinecall.domain.errors import DeclarationRefused
from pinecall.wire import events
from pinecall.wire.events import EPHEMERAL_EVENTS, EVENTS, TERMINAL_EVENT, CallStarted, event_of
from pinecall.wire.frames import Entry, read_log
from pinecall_protocol import events as their_events
from pinecall_protocol import registry, room
from tests.wire.parity import GOLDEN_LOG, mismatches


def test_every_event_and_every_fact_of_the_room_is_the_generated_one_field_for_field() -> None:
    assert mismatches(events, their_events, room) == []


def test_the_registry_is_the_generated_one() -> None:
    assert {name: model.__name__ for name, model in EVENTS.items()} == {
        name: model.__name__ for name, model in registry.EVENTS.items()
    }
    assert EPHEMERAL_EVENTS == registry.EPHEMERAL_EVENTS
    assert TERMINAL_EVENT == registry.TERMINAL_EVENT


def test_every_entry_of_the_golden_log_is_the_model_its_type_names() -> None:
    entries = read_log(GOLDEN_LOG.read_text(encoding="utf-8"))
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
