"""What a call cost, in euros: the table, the two cache prices, and the model nobody listed."""

import pytest
from livekit.agents.metrics.usage import AgentSessionUsage
from livekit.agents.metrics.usage import LLMModelUsage as LiveUsage

from pinecall.providers import prices
from pinecall.providers.usage_wire import as_wire_rows
from pinecall_protocol.metrics import (
    EOTModelUsage,
    InterruptionModelUsage,
    LLMModelUsage,
    STTModelUsage,
    TTSModelUsage,
)

pytestmark = pytest.mark.unit

A_MILLION = 1_000_000


def a_row(model: str, **counted: int) -> LLMModelUsage:
    """One usage row as call.summary carries it."""
    return LLMModelUsage(type="llm_usage", provider="anthropic", model=model, **counted)


def test_a_cache_read_and_a_cache_write_are_priced_apart() -> None:
    """Folding them into the input price would misprice every cached call, in both directions.
    input_tokens is the plugin's sum of the three — half a million fresh, half a million read
    back, a million written — so the fresh line is what is left after both cache lines."""
    cost = prices.cost_of(
        [
            a_row(
                "claude-haiku-4-5-20251001",
                input_tokens=A_MILLION // 2 + A_MILLION // 2 + A_MILLION,
                input_cached_tokens=A_MILLION // 2,
                input_cache_creation_tokens=A_MILLION,
                output_tokens=A_MILLION,
            )
        ]
    )
    by_unit = {row.unit: row for row in cost.rows}
    assert by_unit["input_tokens"].quantity == A_MILLION // 2
    assert by_unit["cache_creation_tokens"].quantity == A_MILLION
    assert by_unit["cached_input_tokens"].unit_price_usd == 0.10
    assert by_unit["cache_creation_tokens"].unit_price_usd == 1.25
    assert by_unit["output_tokens"].unit_price_usd == 5.00
    assert cost.eur == pytest.approx(
        (0.5 * 1.00 + 0.5 * 0.10 + 1.25 + 5.00) * prices.USD_TO_EUR, rel=1e-6
    )


def test_a_model_the_table_does_not_know_is_listed_unpriced_and_never_at_zero() -> None:
    cost = prices.cost_of([a_row("some-model-nobody-listed", input_tokens=1000)])
    assert cost.rows == []
    assert cost.unpriced[0].model == "some-model-nobody-listed"
    assert cost.eur == 0


def test_a_model_is_priced_by_the_longest_prefix_that_matches_it() -> None:
    """A model id carries a date, so the table lists families and the date rides along."""
    assert prices.price_of("claude-fable-5-1-20260901") is prices.PRICES["claude-fable-5-1"]
    # The published list is read by the same rule: a dated snapshot is priced by its family.
    assert prices.price_of("gpt-4o-mini-2024-07-18") == prices.price_of("gpt-4o-mini")
    assert prices.price_of("gpt-4o-2024-11-20") == prices.price_of("gpt-4o")
    assert prices.price_of("llama-3") is None


def test_the_rate_and_the_date_travel_with_the_number() -> None:
    """A cost nobody can reproduce is a rumour; the rate that made it is in the entry."""
    cost = prices.cost_of([a_row("claude-haiku-4-5", input_tokens=1000, output_tokens=100)])
    assert cost.rate.currency == "EUR"
    assert cost.rate.usd_to_eur == prices.USD_TO_EUR
    assert cost.rate.as_of == prices.AS_OF


def test_a_unit_with_nothing_counted_is_not_a_line_of_the_bill() -> None:
    cost = prices.cost_of([a_row("claude-haiku-4-5", input_tokens=1000)])
    assert [row.unit for row in cost.rows] == ["input_tokens"]


def test_a_voice_is_priced_by_the_characters_it_spoke_at_the_pages_price_per_thousand() -> None:
    spoken = TTSModelUsage(
        type="tts_usage", provider="ElevenLabs", model="eleven_flash_v2_5", characters_count=2000
    )
    cost = prices.cost_of([spoken])
    (row,) = cost.rows
    assert (row.unit, row.quantity) == ("characters", 2000)
    assert row.unit_price_usd == pytest.approx(0.05 / 1000)
    assert cost.eur == pytest.approx(2 * 0.05 * prices.USD_TO_EUR, rel=1e-6)


def test_the_ears_are_priced_by_the_seconds_they_heard_at_the_pages_price_per_hour() -> None:
    heard = STTModelUsage(
        type="stt_usage", provider="Soniox", model="stt-rt-v5", audio_duration=90.0
    )
    cost = prices.cost_of([heard])
    (row,) = cost.rows
    assert (row.unit, row.quantity) == ("audio_seconds", 90.0)
    assert cost.eur == pytest.approx(90 / 3600 * 0.12 * prices.USD_TO_EUR, rel=1e-6)


# Cartesia used to be the example here, because nobody had read its pricing page. The published
# list has, so the example is now a model nobody anywhere has listed — which is what `unpriced`
# was always for: a bill this build cannot state, said out loud rather than counted as zero.
def test_a_voice_nobody_listed_anywhere_is_unpriced_and_never_free() -> None:
    cost = prices.cost_of(
        [TTSModelUsage(type="tts_usage", provider="Acme", model="acme-1", characters_count=10)]
    )
    assert cost.rows == [] and cost.unpriced[0].model == "acme-1"


# The published list is what makes the other forty vendors cost something: a call spoken by
# Cartesia read `unpriced` until it was there, and an unpriced call is a bill nobody can see.
def test_a_vendor_this_build_read_no_page_for_is_priced_off_the_published_list() -> None:
    spoken = TTSModelUsage(
        type="tts_usage", provider="Cartesia", model="sonic-3", characters_count=1_000
    )
    (row,) = prices.cost_of([spoken]).rows
    assert (row.unit, row.quantity) == ("characters", 1_000)
    assert row.eur == pytest.approx(1_000 * 5e-05 * prices.USD_TO_EUR, rel=1e-6)


# Ours first, and this is the row that says why: the published list prices Soniox by the token,
# which an audio-seconds usage row cannot feed, so the hand-read hourly rate has to win.
def test_the_hand_read_table_wins_over_the_published_one_where_they_disagree() -> None:
    assert prices.media_price_of("stt-rt-v5") == prices.MEDIA_PRICES["stt-rt"]
    assert prices.media_price_of("flux-general-multi") == prices.MEDIA_PRICES["flux-general"]


def test_livekits_own_turn_models_owe_nothing_and_are_neither_a_line_nor_unpriced() -> None:
    """Interruption and end-of-turn run on this box, in livekit's inference/: there is no bill."""
    cost = prices.cost_of(
        [
            EOTModelUsage(type="eot_usage", provider="livekit", model="turn-detector-v1-mini"),
            InterruptionModelUsage(type="interruption_usage", provider="livekit", model="adaptive"),
        ]
    )
    assert (cost.rows, cost.unpriced, cost.eur) == ([], [], 0)


def test_one_call_bills_its_three_vendors_together() -> None:
    """The reason cost_of takes every row: a summary that priced the model alone said too little."""
    cost = prices.cost_of(
        [
            a_row("claude-haiku-4-5", input_tokens=1000, output_tokens=100),
            TTSModelUsage(
                type="tts_usage",
                provider="ElevenLabs",
                model="eleven_flash_v2_5",
                characters_count=500,
            ),
            STTModelUsage(
                type="stt_usage",
                provider="Deepgram",
                model="flux-general-multi",
                audio_duration=60.0,
            ),
        ]
    )
    assert {row.unit for row in cost.rows} == {
        "input_tokens",
        "output_tokens",
        "characters",
        "audio_seconds",
    }
    assert cost.unpriced == []


# A live Haiku call wrote `provider: "api.anthropic.com"` into its summary, and the question
# was whether that left the row unpriced. It does not — the table is keyed by model
# prefix, never by the label — and this pins it from livekit's own object, with no key and no call.
def test_a_row_livekit_labelled_with_the_api_host_is_priced_by_its_model_all_the_same() -> None:
    """livekit's anthropic and openai plugins answer `provider` with the base URL's netloc."""
    measured = LiveUsage(provider="api.anthropic.com", model="claude-haiku-4-5-20251001")
    measured.input_tokens = A_MILLION
    measured.output_tokens = A_MILLION
    cost = prices.cost_of(as_wire_rows(AgentSessionUsage(model_usage=[measured]).model_usage))
    assert cost.unpriced == []
    assert cost.eur > 0
    assert {row.provider for row in cost.rows} == {"api.anthropic.com"}


# ── what the judges cost, on call.score ─────────────────────────────────────────


# A judgment carries no cost of its own (livekit/agents/evals/judge.py:34-57), so ring 4 prices
# the judge's tokens here like any other LLM row — and an unknown bill is ABSENT from the entry
# rather than written as zero. docs/decisions/scoring.md.
def test_the_judges_bill_is_the_same_table_every_other_llm_row_is_priced_by() -> None:
    """One million Haiku input tokens, priced once, wherever the tokens were spent."""
    asked = [a_row("claude-haiku-4-5-20251001", input_tokens=A_MILLION)]
    assert prices.eur_of(asked) == prices.cost_of(asked).eur > 0


def test_a_judge_nobody_reported_a_token_for_has_no_bill_and_never_a_bill_of_zero() -> None:
    """Nothing to price is not something that cost nothing: the field goes missing instead."""
    assert prices.eur_of([]) is None
    assert prices.eur_of([a_row("a-model-nobody-listed", input_tokens=A_MILLION)]) is None
