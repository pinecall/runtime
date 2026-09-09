"""The matrix: two models, two goldens, two judges, and the call's own summary beside each cell."""

from __future__ import annotations

from pinecall.evals import ConsentJudge, Matrix, RegisterJudge, Spoken, a_case, a_matrix
from tests.evals.fakes import CountingJudge
from tests.evals.logs import BOOKING, a_log
from tests.evals.measuring import measured

HAIKU = "claude-haiku-4-5"
SONNET = "claude-sonnet-4-6"

CONSENT = "consent"
REGISTER = "register"


def the_cases() -> list[Spoken]:
    """The same two goldens under two models: a matrix is what tells them apart."""
    confirmed = a_case(a_log("booking-confirmed"), tools=BOOKING)
    before_the_yes = a_case(a_log("booking-before-the-yes"), tools=BOOKING)
    return [
        Spoken(model=HAIKU, golden="confirmed", case=confirmed),
        Spoken(model=HAIKU, golden="before-the-yes", case=before_the_yes),
        Spoken(model=SONNET, golden="confirmed", case=confirmed),
        Spoken(model=SONNET, golden="before-the-yes", case=before_the_yes),
    ]


# Consent reads a gate and register reads the words, so one judge of each is enough to prove the
# axes; the gate of the first case is the one both goldens are asked about here on purpose — a
# matrix that judged each cell with its own gate would not tell two models apart at all.
async def a_matrix_of_the_two_judges() -> Matrix:
    spoken = the_cases()
    return await a_matrix(
        spoken,
        [ConsentJudge(spoken[1].case.gate), RegisterJudge("usted")],
        CountingJudge(),
    )


async def test_the_axes_come_out_in_the_order_the_runs_came_in() -> None:
    matrix = await a_matrix_of_the_two_judges()

    assert matrix.models == (HAIKU, SONNET)
    assert matrix.goldens == ("confirmed", "before-the-yes")
    assert matrix.metrics == (CONSENT, REGISTER)


async def test_a_cell_answers_for_one_model_on_one_golden() -> None:
    matrix = await a_matrix_of_the_two_judges()

    held = matrix.at(HAIKU, "confirmed")
    broke = matrix.at(SONNET, "before-the-yes")
    assert held is not None and broke is not None

    kept, missed = held.at(REGISTER), broke.at(REGISTER)
    assert kept is not None and missed is not None
    assert (kept.score, missed.score) == (1.0, 0.0)
    assert broke.at("a judge nobody ran") is None


async def test_the_findings_are_every_judge_that_did_not_hold_with_the_run_it_broke_on() -> None:
    matrix = await a_matrix_of_the_two_judges()

    failures = matrix.failures()
    # That log breaks register on both cells it is drawn in — it says "Ya te la reservé" to a
    # caller the clinic addresses as usted — and the gate it was judged with breaks consent
    # everywhere, which is the point: the same judge over four cells answers four times.
    assert {(run.model, run.golden, score.metric) for run, score in failures} == {
        (HAIKU, "confirmed", CONSENT),
        (HAIKU, "before-the-yes", CONSENT),
        (HAIKU, "before-the-yes", REGISTER),
        (SONNET, "confirmed", CONSENT),
        (SONNET, "before-the-yes", CONSENT),
        (SONNET, "before-the-yes", REGISTER),
    }


async def test_a_matrix_of_hard_policies_asks_nothing_and_says_so() -> None:
    judge = CountingJudge()
    spoken = the_cases()

    matrix = await a_matrix(spoken, [ConsentJudge(spoken[0].case.gate)], judge)

    assert judge.prompts == []
    assert matrix.judge_calls == 0


async def test_every_cell_carries_the_calls_own_summary_and_never_a_number_of_its_own() -> None:
    """These logs end at `call.ended`, so the summary is absent — and absent, never a zero."""
    matrix = await a_matrix_of_the_two_judges()

    assert all(run.summary is None for run in matrix.runs)


async def test_a_judge_is_named_by_itself_so_two_judges_can_never_share_a_column() -> None:
    """The metric is the judge's own `name` (evals/evaluation.py:20-23), never a caller's key."""
    case = a_case(a_log("booking-confirmed"), tools=BOOKING)

    score = await measured(RegisterJudge("usted"), case, CountingJudge())

    assert score.metric == REGISTER
