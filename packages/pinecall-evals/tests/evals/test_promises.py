"""Promises: a call that commits to nothing costs nothing; a commitment is weighed against tools."""

from __future__ import annotations

import pytest

from pinecall.evals.judges.promises import committed_in, promises_of
from tests.evals.conversations import a_call, a_case_of, asked, replied
from tests.evals.fakes import CountingJudge
from tests.evals.measuring import measured

pytestmark = pytest.mark.unit


async def test_a_call_that_promises_nothing_holds_and_asks_nobody() -> None:
    judge = CountingJudge(verdict="fail")
    case = a_case_of(asked("¿Abren el sábado?"), replied("Sí, de nueve a dos."))

    score = await measured(promises_of(case), case, judge)

    assert (score.score, judge.prompts) == (1.0, [])


async def test_a_callback_nobody_booked_reaches_the_judge_with_every_tool_call() -> None:
    """The judge decides whether the commitment is recorded, and it reads what the tools did."""
    judge = CountingJudge(verdict="fail", reason="promised a call back and no tool booked one")
    case = a_case_of(
        asked("No me va bien ahora."),
        replied(
            "Sin problema, le llamaremos mañana por la mañana.",
            calls=[a_call("lookupClient", {"phone": "600"}, {"name": "Ana"})],
        ),
    )

    score = await measured(promises_of(case), case, judge)

    assert (score.score, score.judge_calls) == (0.0, 1)
    assert 'lookupClient({"phone": "600"})' in judge.prompts[0]


def test_the_phrases_that_commit_are_found_in_both_languages() -> None:
    said = [
        "Perfecto, un técnico pasará el martes.",
        "I'll call you back tomorrow.",
        "La revisión le costará 45 euros.",
        "¿Me dice su nombre?",
    ]
    assert committed_in(said) == ("un técnico pasará", "I'll call", "le costará")
