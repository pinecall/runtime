"""Tests for the commands: the sockets, what each produces, and how one is read."""

import pytest
from pydantic import ValidationError

from pinecall.domain.errors import DeclarationRefused
from pinecall.wire.commands import COMMANDS, PRODUCES, CallAttention, CallDial, Ping, command_of
from pinecall.wire.events import EVENTS
from pinecall.wire.frames import Command


def test_every_command_produces_known_events() -> None:
    assert set(PRODUCES) == set(COMMANDS)
    assert all(produced in EVENTS for names in PRODUCES.values() for produced in names)


def test_a_command_is_read_as_the_model_its_type_names_or_refused() -> None:
    dial = command_of(Command(type="call.dial", agent="a", call=None, data={"to": "+34600000001"}))
    assert isinstance(dial, CallDial)
    with pytest.raises(DeclarationRefused, match=r"call\.dial"):
        command_of(Command(type="call.dial", agent="a", call=None, data={"to": 1}))
    with pytest.raises(DeclarationRefused, match="unknown command type"):
        command_of(Command(type="call.teleport", agent="a", call=None, data={}))


def test_an_unknown_key_is_refused_because_the_schema_says_additional_properties_false() -> None:
    with pytest.raises(DeclarationRefused, match="ping"):
        Ping.read({"extra": True}, "ping")


def test_a_call_waits_for_a_person_fifteen_minutes_at_most() -> None:
    assert CallAttention(reason="a refund", wait_s=900).wait_s == 900
    for seconds in (0, 901):
        with pytest.raises(ValidationError):
            CallAttention(reason="a refund", wait_s=seconds)
