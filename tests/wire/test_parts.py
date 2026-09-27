"""Tests for the shared parts: the wire's words and its common shapes."""

from pinecall.wire import parts
from pinecall.wire.parts import EventSpec
from pinecall_protocol import defs
from tests.wire.parity import mismatches


def test_every_shared_shape_is_the_generated_one_field_for_field() -> None:
    assert mismatches(parts, defs) == []


def test_a_key_python_cannot_spell_travels_by_its_wire_name() -> None:
    spec = EventSpec.read({"name": "slot.released", "from": ["app"]}, "event")
    assert spec.from_ == ["app"]
    assert spec.written() == {"name": "slot.released", "from": ["app"]}
