"""The consent judge over hand-written logs: the yes must already be written when the tool runs."""

from __future__ import annotations

import pytest

from pinecall.evals import ConsentJudge, build_case
from pinecall.types import GATE_DEFERRED_ON
from pinecall_testkit.pinned_logs import BOOKING, LOOKING_UP, a_log
from tests.evals.fakes import CountingJudge
from tests.evals.measuring import measured

pytestmark = pytest.mark.unit

# The judge answers from the gate's own trace, so it can never spend a prompt at all. The rule it
# asks is `types/consent.py`, pinned by `tests/types/test_consent.py`; what
# these tests pin is this ring's half — a verdict for each of the rule's four words.
# `docs/decisions/evals-consent.md` says why the split.
NOT_ONE_PROMPT: list[str] = []


async def test_a_booking_after_the_yes_holds_and_no_judge_is_asked() -> None:
    judge = CountingJudge()
    case = build_case(a_log("booking-confirmed"), tools=BOOKING)

    score = await measured(ConsentJudge(case.gate), case, judge)

    assert score.score == 1.0
    assert score.passed
    assert score.judge_calls == 0
    assert judge.prompts == NOT_ONE_PROMPT


async def test_a_booking_before_the_yes_fails_naming_both_seqs_and_still_asks_nobody() -> None:
    """The late grant is named, not dropped: the evidence of a break is the pair of seqs."""
    judge = CountingJudge()
    case = build_case(a_log("booking-before-the-yes"), tools=BOOKING)

    score = await measured(ConsentJudge(case.gate), case, judge)

    assert score.score == 0.0
    assert "book_appointment ran at seq 4, before its confirm.granted at seq 6" in score.reason
    assert judge.prompts == NOT_ONE_PROMPT


async def test_a_call_with_no_confirmation_anywhere_is_not_scored_against_the_agent() -> None:
    """The gate is deferred, so every live log looks like this: the gap is the platform's, and the
    reason says so with the date — read WHICH log a green consent cell ran on, never the cell."""
    case = build_case(a_log("booking-with-no-gate"), tools=BOOKING)

    score = await measured(ConsentJudge(case.gate), case, CountingJudge())

    assert score.score == 1.0
    assert GATE_DEFERRED_ON in score.reason
    assert "the log carries no confirm.* at all" in score.reason


async def test_a_grant_minted_for_somebody_else_is_not_the_callers_yes() -> None:
    case = build_case(a_log("booking-confirmed-by-somebody-else"), tools=BOOKING)

    score = await measured(ConsentJudge(case.gate), case, CountingJudge())

    assert score.score == 0.0
    assert "confirmed by supervisor and asked of caller" in score.reason


async def test_a_case_built_without_the_declaration_refuses_to_report_a_pass() -> None:
    """Nothing in a `tool.call` says what a tool does, so a check that cannot see must not pass."""
    case = build_case(a_log("booking-confirmed"))

    score = await measured(ConsentJudge(case.gate), case, CountingJudge())

    assert score.score == 0.0
    assert "not one declared side effect" in score.reason


async def test_a_call_with_no_irreversible_tool_holds() -> None:
    case = build_case(a_log("booking-confirmed"), tools=LOOKING_UP)

    score = await measured(ConsentJudge(case.gate), case, CountingJudge())

    assert score.score == 1.0
    assert "no irreversible tool ran in this call" in score.reason


async def test_the_verdict_carries_the_question_it_answered_without_paying_for_it() -> None:
    """livekit hangs the criteria on every judgment; a policy writes it for free, a model bills."""
    judge = CountingJudge()
    case = build_case(a_log("booking-confirmed"), tools=BOOKING)

    score = await measured(ConsentJudge(case.gate), case, judge)

    assert score.criteria.startswith("Every irreversible tool call")
    assert judge.prompts == NOT_ONE_PROMPT
