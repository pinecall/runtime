"""Tests for the commands: every one is the generated one, the verbs, and what each produces."""

import pytest

from pinecall.domain.errors import DeclarationRefused
from pinecall.wire import commands
from pinecall.wire.commands import COMMANDS, PRODUCES, CallDial, Ping, command_of
from pinecall.wire.events import EVENTS
from pinecall.wire.frames import Command
from pinecall_protocol import commands as their_commands
from pinecall_protocol import registry, verbs
from tests.wire.parity import mismatches


def test_every_command_and_every_verb_is_the_generated_one_field_for_field() -> None:
    assert mismatches(commands, their_commands, verbs) == []


def test_the_registry_is_the_generated_one_and_every_command_produces_known_events() -> None:
    assert {name: model.__name__ for name, model in COMMANDS.items()} == {
        name: model.__name__ for name, model in registry.COMMANDS.items()
    }
    assert PRODUCES == registry.PRODUCES
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
