"""Tests for the shared parts: the wire's words and its common shapes."""

from pinecall.wire.parts import EventSpec


def test_a_key_python_cannot_spell_travels_by_its_wire_name() -> None:
    spec = EventSpec.read({"name": "slot.released", "from": ["app"]}, "event")
    assert spec.from_ == ["app"]
    assert spec.written() == {"name": "slot.released", "from": ["app"]}
