"""A call's usage priced from the box's rates: never at zero when unknown, never free."""

from pinecall.providers.catalog import Providers, Rate
from pinecall.providers.prices import cost, rate_of
from pinecall.wire.metrics import (
    EOTModelUsage,
    InterruptionModelUsage,
    LLMModelUsage,
    STTModelUsage,
    TTSModelUsage,
)


def test_a_cache_read_and_a_cache_write_are_priced_apart(configured: Providers) -> None:
    used = LLMModelUsage(
        provider="anthropic",
        model="claude-haiku-4-5",
        input_tokens=1_000_000,
        input_cached_tokens=400_000,
        input_cache_creation_tokens=100_000,
        output_tokens=0,
    )
    rows = {row.unit: row for row in cost([used], configured).rows}
    assert rows["input_tokens"].quantity == 500_000
    assert rows["cached_input_tokens"].unit_price_usd == 0.1
    assert "cache_creation_tokens" not in rows


def test_a_model_the_rates_do_not_know_is_listed_unpriced_and_never_at_zero(
    configured: Providers,
) -> None:
    priced = cost([LLMModelUsage(provider="x", model="nobody-1", input_tokens=10)], configured)
    assert priced.rows == []
    assert [(row.provider, row.model) for row in priced.unpriced] == [("x", "nobody-1")]


def test_a_model_is_priced_by_the_longest_prefix_that_matches_it() -> None:
    rates = {"claude": Rate(input=9.0), "claude-haiku-4-5": Rate(input=1.0)}
    found = rate_of(rates, "claude-haiku-4-5-20251001")
    assert found is not None
    assert found.input == 1.0
    assert rate_of(rates, "gpt-5") is None


def test_nothing_used_costs_nothing_and_lists_nothing(configured: Providers) -> None:
    priced = cost([], configured)
    assert (priced.usd, priced.rows, priced.unpriced) == (0.0, [], [])


def test_a_unit_with_nothing_counted_is_not_a_line_of_the_bill(configured: Providers) -> None:
    text = TTSModelUsage(provider="cartesia", model="sonic-3", characters_count=0)
    priced = cost([text], configured)
    assert (priced.rows, priced.unpriced) == ([], [])


def test_a_voice_is_priced_by_the_characters_it_spoke(configured: Providers) -> None:
    text = TTSModelUsage(provider="cartesia", model="sonic-3", characters_count=1000)
    (row,) = cost([text], configured).rows
    assert (row.unit, row.quantity, row.usd) == ("characters", 1000, 0.03)


def test_the_ears_are_priced_by_the_seconds_they_heard(configured: Providers) -> None:
    heard = STTModelUsage(provider="deepgram", model="flux-general-multi", audio_duration=60.0)
    (row,) = cost([heard], configured).rows
    assert (row.unit, row.quantity, row.usd) == ("audio_seconds", 60.0, 0.006)


def test_a_voice_nobody_priced_is_unpriced_and_never_free(configured: Providers) -> None:
    text = TTSModelUsage(provider="hume", model="octave", characters_count=10)
    assert [row.model for row in cost([text], configured).unpriced] == ["octave"]


def test_livekits_own_turn_models_owe_nothing_and_are_neither_a_line_nor_unpriced(
    configured: Providers,
) -> None:
    turns = [
        EOTModelUsage(provider="livekit", model="v1-mini", total_requests=40),
        InterruptionModelUsage(provider="livekit", model="vad", total_requests=3),
    ]
    priced = cost(turns, configured)
    assert (priced.rows, priced.unpriced) == ([], [])


def test_one_call_bills_its_three_vendors_together(configured: Providers) -> None:
    usage = [
        LLMModelUsage(provider="a", model="claude-haiku-4-5", input_tokens=1000, output_tokens=100),
        TTSModelUsage(provider="c", model="sonic-3", characters_count=1000),
        STTModelUsage(provider="d", model="flux-general-multi", audio_duration=60.0),
    ]
    priced = cost(usage, configured)
    assert len(priced.rows) == 4
    assert priced.usd == round(sum(row.usd for row in priced.rows), 6)


def test_a_row_labelled_with_the_api_host_is_priced_by_its_model_all_the_same(
    configured: Providers,
) -> None:
    used = LLMModelUsage(provider="api.anthropic.com", model="claude-haiku-4-5", output_tokens=10)
    assert [row.provider for row in cost([used], configured).rows] == ["api.anthropic.com"]
