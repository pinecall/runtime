"""The usage fold: a call.summary and a call.score become one row each, and rows sum per org."""

import pytest

from pinecall.log.entry import Entry
from pinecall.log.store import Metered
from pinecall.log.usage import METERED_TYPES, UNOWNED, fold_usage_row, totals_by_org
from pinecall.types.json import JsonObject
from pinecall_testkit.usage import A_SCORE, A_SCORE_NOBODY_JUDGED, A_SUMMARY

pytestmark = pytest.mark.unit


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
    row = fold_usage_row(metered(7, "clinica", "call.summary", A_SUMMARY))
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
    row = fold_usage_row(metered(8, "clinica", "call.score", A_SCORE))
    assert (row.judge_calls, row.cost_eur) == (2, pytest.approx(0.001))
    assert (row.minutes, row.messages, row.input_tokens, row.characters) == (0.0, 0, 0, 0)


def test_a_log_nobody_claimed_is_filed_as_unowned_rather_than_dropped() -> None:
    assert fold_usage_row(metered(1, None, "call.summary", A_SUMMARY)).org == UNOWNED


def test_totals_sum_per_org_and_count_a_call_once_for_its_summary() -> None:
    rows = [
        fold_usage_row(metered(1, "clinica", "call.summary", A_SUMMARY, "CA_1")),
        fold_usage_row(metered(2, "clinica", "call.score", A_SCORE, "CA_1")),
        fold_usage_row(metered(3, "tienda", "call.summary", A_SUMMARY, "CA_2")),
        fold_usage_row(metered(4, "clinica", "call.summary", A_SUMMARY, "CA_3")),
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


def test_a_score_nobody_judged_costs_nothing_instead_of_raising() -> None:
    """The 500 this fixes: `float(None)` on a key that is present and null. Most calls end this
    way — nobody judged them — so one of these rows took the whole Usage page down with it."""
    row = fold_usage_row(metered(1, "acme", "call.score", A_SCORE_NOBODY_JUDGED))

    assert row.judge_calls == 0
    assert row.cost_eur == 0.0


def test_a_page_of_rows_survives_one_unjudged_score_among_them() -> None:
    """It is a page, and one row that cannot be folded is a page nobody can read."""
    rows = [
        fold_usage_row(metered(1, "acme", "call.summary", A_SUMMARY)),
        fold_usage_row(metered(2, "acme", "call.score", A_SCORE_NOBODY_JUDGED)),
        fold_usage_row(metered(3, "acme", "call.score", A_SCORE)),
    ]

    totals = totals_by_org(rows)["acme"]

    assert totals.calls == 1
    assert totals.judge_calls == 2
    assert totals.cost_eur == pytest.approx(0.013)
