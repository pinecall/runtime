"""The report: one HTML page, the findings a person actually reads, and no number of its own."""

from __future__ import annotations

from pinecall.evals import ConsentJudge, Spoken, a_case, a_matrix, as_html
from tests.evals.fakes import CountingJudge
from tests.evals.logs import BOOKING, THE_GOLDENS_TOOLS, a_log, the_golden_call


async def a_report() -> str:
    """Two goldens under one model, one judge, drawn: the smallest page worth asserting on."""
    before_the_yes = a_case(a_log("booking-before-the-yes"), tools=BOOKING)
    matrix = await a_matrix(
        [
            Spoken(
                model="haiku",
                golden="before-the-yes",
                case=before_the_yes,
            ),
            Spoken(
                model="haiku",
                golden="confirmed",
                case=a_case(a_log("booking-confirmed"), tools=BOOKING),
            ),
        ],
        [ConsentJudge(before_the_yes.gate)],
        CountingJudge(),
    )
    return as_html(matrix, title="Clínica Norte")


async def test_the_page_stands_alone_with_no_stylesheet_and_no_script_to_fetch() -> None:
    """It is opened from a terminal or mailed to somebody; either may have no network."""
    page = await a_report()

    assert page.startswith("<!doctype html>")
    assert "<script" not in page
    assert "http://" not in page and "https://" not in page


async def test_a_cell_is_red_when_the_judge_did_not_hold() -> None:
    page = await a_report()

    assert '<td class="broken">0.00</td>' in page


async def test_the_findings_carry_the_reason_the_policy_wrote_for_nothing() -> None:
    page = await a_report()

    assert "book_appointment ran at seq 4, before its confirm.granted at seq 6" in page
    assert "put 0 question(s) to a model" in page


async def test_the_call_row_is_read_off_the_calls_own_summary() -> None:
    """A number on this page is a number the log wrote; a call with no summary shows a dash."""
    golden = a_case(the_golden_call(), tools=THE_GOLDENS_TOOLS)
    matrix = await a_matrix(
        [Spoken(model="haiku", golden="the-golden-call", case=golden)],
        [ConsentJudge(golden.gate)],
        CountingJudge(),
    )

    page = as_html(matrix)

    assert "booked BK-5521" in page
    assert "<td>11</td>" in page
    assert "<td>52.0</td>" in page


async def test_a_page_where_everything_held_says_so_instead_of_listing_nothing() -> None:
    confirmed = a_case(a_log("booking-confirmed"), tools=BOOKING)
    matrix = await a_matrix(
        [Spoken(model="haiku", golden="confirmed", case=confirmed)],
        [ConsentJudge(confirmed.gate)],
        CountingJudge(),
    )

    page = as_html(matrix)

    assert '<td class="held">1.00</td>' in page
    assert "Every judge held on every golden." in page
