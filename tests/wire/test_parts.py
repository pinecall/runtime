"""Tests for the shared parts: the wire's words and its common shapes."""

from pinecall.domain.names import JsonObject
from pinecall.wire.parts import Cost, EventSpec


def test_a_key_python_cannot_spell_travels_by_its_wire_name() -> None:
    spec = EventSpec.read({"name": "slot.released", "from": ["app"]}, "event")
    assert spec.from_ == ["app"]
    assert spec.written() == {"name": "slot.released", "from": ["app"]}


def test_a_cost_written_in_euros_reads_as_the_same_dollars_and_is_written_in_dollars() -> None:
    row: JsonObject = {
        "provider": "api.anthropic.com",
        "model": "claude-haiku-4-5",
        "unit": "output_tokens",
    }
    priced: JsonObject = {**row, "quantity": 100.0, "unit_price_usd": 5.0}
    rate: JsonObject = {"as_of": "2026-09-06", "currency": "EUR", "usd_to_eur": 0.92}
    cost = Cost.read(
        {"eur": 0.5, "rate": rate, "rows": [{**priced, "eur": 0.5}], "unpriced": []}, "c"
    )
    assert (cost.usd, cost.rows[0].usd) == (0.5, 0.5)
    assert cost.written() == {"usd": 0.5, "rows": [{**priced, "usd": 0.5}], "unpriced": []}
