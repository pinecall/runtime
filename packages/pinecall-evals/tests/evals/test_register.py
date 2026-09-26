"""The register scan: a clinic asked for usted, a salon asked for tú, and neither wants both."""

from __future__ import annotations

import pytest

from pinecall.evals import RegisterJudge
from pinecall.evals.judges.register import Register
from tests.evals.conversations import a_case_of, asked, replied
from tests.evals.fakes import CountingJudge
from tests.evals.measuring import measured

pytestmark = pytest.mark.unit

HELLO = "Hola, quería una cita."


async def a_score(said: str, expected: Register, judge: CountingJudge) -> float:
    """One caller's line and one reply, which is all a register question is ever about."""
    case = a_case_of(asked(HELLO), replied(said))
    return (await measured(RegisterJudge(expected), case, judge)).score


async def test_an_agent_asked_for_usted_that_keeps_it_holds_without_a_judge() -> None:
    judge = CountingJudge()

    assert await a_score("Claro, usted dirá. ¿Su nombre?", "usted", judge) == 1.0
    assert judge.prompts == []


async def test_an_agent_asked_for_usted_that_tutea_is_caught_word_by_word() -> None:
    case = a_case_of(asked(HELLO), replied("Vale, dime tu nombre."))

    score = await measured(RegisterJudge("usted"), case, CountingJudge())

    assert score.score == 0.0
    assert "'tu' in agent turn 1" in score.reason
    assert "asked for usted" in score.reason


async def test_an_agent_asked_for_tu_that_slips_into_usted_is_caught_too() -> None:
    assert await a_score("Cuando usted quiera.", "tu", CountingJudge()) == 0.0


async def test_a_turn_that_marks_neither_register_is_not_a_slip() -> None:
    """Half of what an agent says addresses nobody in particular, and that is not a fault."""
    assert await a_score("Un momento, lo compruebo.", "usted", CountingJudge()) == 1.0


async def test_a_word_that_only_contains_a_marker_is_not_the_marker() -> None:
    """A business that bans tuteo has not banned `tutor`, and a substring scan would say it had."""
    assert await a_score("Le paso con el tutor del paciente.", "usted", CountingJudge()) == 1.0


async def test_the_ambiguous_third_person_words_are_left_out_on_purpose() -> None:
    """`su` and `le` are polite as often as they are third person; counting them would invent
    a register the agent never chose."""
    assert await a_score("Le confirmo su cita.", "tu", CountingJudge()) == 1.0
