"""The usage fold: a call.summary and a call.score become one row each, and rows sum per org."""

import pytest

from pinecall.log.entry import Entry
from pinecall.log.store import Metered
from pinecall.log.usage import METERED_TYPES, UNOWNED, a_usage_row, totals_by_org
from pinecall.types.json import JsonObject

pytestmark = pytest.mark.unit

A_SUMMARY: JsonObject = {
    "reason": "hangup",
    "outcome": "booked",
    "duration_s": 90.0,
    "turns": 6,
    "usage": [
        {
            "type": "llm",
            "provider": "anthropic",
            "model": "h",
            "input_tokens": 1200,
            "output_tokens": 300,
        },
        {"type": "tts", "provider": "elevenlabs", "model": "v3", "characters_count": 450},
        {"type": "stt", "provider": "soniox", "model": "x", "audio_duration": 88.0},
    ],
    "cost": {"eur": 0.012, "rate": {}, "rows": [], "unpriced": []},
}
A_SCORE: JsonObject = {"passed": True, "judges": [], "judge_calls": 2, "judge_cost_eur": 0.001}


def metered(
    position: int, org: str | None, type: str, data: JsonObject, call: str = "CA_1"
) -> Metered:
    """One row as the store hands it across every log."""
    entry = Entry(
        seq=position,
        ts=1.0,
        call=call,
        agent="clinica-norte",
        type=type,
        ephemeral=False,
        data=data,
    )
    return Metered(position=position, org=org, entry=entry)


def test_a_summary_folds_into_minutes_turns_tokens_and_characters() -> None:
    row = a_usage_row(metered(7, "clinica", "call.summary", A_SUMMARY))
    assert (row.cursor, row.org, row.call, row.type) == (7, "clinica", "CA_1", "call.summary")
    assert row.minutes == pytest.approx(1.5)
    assert (row.messages, row.input_tokens, row.output_tokens, row.characters) == (
        6,
        1200,
        300,
        450,
    )
    assert row.cost_eur == pytest.approx(0.012)
    assert row.judge_calls == 0


def test_a_score_folds_into_judge_calls_and_nothing_else() -> None:
    row = a_usage_row(metered(8, "clinica", "call.score", A_SCORE))
    assert (row.judge_calls, row.cost_eur) == (2, pytest.approx(0.001))
    assert (row.minutes, row.messages, row.input_tokens, row.characters) == (0.0, 0, 0, 0)


def test_a_log_nobody_claimed_is_filed_as_unowned_rather_than_dropped() -> None:
    assert a_usage_row(metered(1, None, "call.summary", A_SUMMARY)).org == UNOWNED


def test_totals_sum_per_org_and_count_a_call_once_for_its_summary() -> None:
    rows = [
        a_usage_row(metered(1, "clinica", "call.summary", A_SUMMARY, "CA_1")),
        a_usage_row(metered(2, "clinica", "call.score", A_SCORE, "CA_1")),
        a_usage_row(metered(3, "tienda", "call.summary", A_SUMMARY, "CA_2")),
        a_usage_row(metered(4, "clinica", "call.summary", A_SUMMARY, "CA_3")),
    ]
    totals = totals_by_org(rows)
    assert list(totals) == ["clinica", "tienda"]
    assert (totals["clinica"].calls, totals["clinica"].judge_calls) == (2, 2)
    assert totals["clinica"].minutes == pytest.approx(3.0)
    assert totals["clinica"].messages == 12
    assert (totals["tienda"].calls, totals["tienda"].input_tokens) == (1, 1200)


def test_the_metered_types_are_the_summary_and_the_score_and_nothing_else() -> None:
    """Every other entry is the conversation; these two are what it consumed."""
    assert METERED_TYPES == ("call.summary", "call.score")
