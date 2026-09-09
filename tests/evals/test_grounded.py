"""Grounded facts: what code can match costs nothing, and only what it cannot reaches a judge."""

from __future__ import annotations

from pinecall.evals import EXTRACTORS, Case, GroundedJudge, evidence_of
from tests.evals.conversations import a_call, a_case_of, asked, replied
from tests.evals.fakes import CountingJudge
from tests.evals.measuring import measured

WHEN = "¿Cuándo tiene hueco?"


def a_judge_for(case: Case) -> GroundedJudge:
    """The judge built for this case's own evidence: the knowledge, the chunks, the tool answers."""
    return GroundedJudge(EXTRACTORS, evidence_of(case))


async def a_score(case: Case, judge: CountingJudge) -> float:
    return (await measured(a_judge_for(case), case, judge)).score


async def test_a_call_whose_every_fact_is_in_the_evidence_scores_one_and_asks_nobody() -> None:
    """The whole reason code answers first: a grounded call is free to check."""
    judge = CountingJudge()
    case = a_case_of(
        asked(WHEN),
        replied(
            "Tengo libre el 13/08 a las 09:30 con la doctora Vidal.",
            calls=[a_call("find_slots", {"day": "13/08"}, {"slots": ["13/08 09:30 Vidal"]})],
        ),
    )

    assert await a_score(case, judge) == 1.0
    assert judge.prompts == []


async def test_a_call_that_stated_no_concrete_fact_holds_without_a_judge() -> None:
    judge = CountingJudge()
    case = a_case_of(asked(WHEN), replied("Claro, dígame su nombre."))

    assert await a_score(case, judge) == 1.0
    assert judge.prompts == []


async def test_a_price_said_in_words_reaches_the_judge() -> None:
    """`45 euros` is not `45 €` to a string, and is the same price to a person: that is the job."""
    judge = CountingJudge(verdict="pass")
    case = a_case_of(
        asked(WHEN), replied("La revisión son 45 euros.", retrieved=["Revisión: 45 €."])
    )

    score = await measured(a_judge_for(case), case, judge)

    assert score.score == 1.0
    assert score.judge_calls == 1
    assert "Revisión: 45 €." in judge.prompts[0]


async def test_the_judge_saying_no_is_the_score_going_to_zero() -> None:
    judge = CountingJudge(verdict="fail")
    case = a_case_of(
        asked(WHEN), replied("La revisión son 45 euros.", retrieved=["Revisión: 60 €."])
    )

    assert await a_score(case, judge) == 0.0
    assert len(judge.prompts) == 1


async def test_a_judge_that_is_unsure_scores_a_half_and_never_a_pass() -> None:
    """livekit's third verdict, kept: `maybe` is not proof, and it is not a finding either."""
    judge = CountingJudge(verdict="maybe")
    case = a_case_of(
        asked(WHEN), replied("La revisión son 45 euros.", retrieved=["Revisión: 60 €."])
    )

    score = await measured(a_judge_for(case), case, judge)

    assert (score.score, score.passed) == (0.5, False)


async def test_an_hour_only_in_the_written_evidence_is_named_as_a_near_miss() -> None:
    """An hour is supposed to come off the agenda; finding it in the knowledge is worth saying."""
    case = a_case_of(
        asked(WHEN), replied("Le puedo dar las 09:30.", retrieved=["Horario: 09:30 a 20:00."])
    )

    unmatched = a_judge_for(case).every_fact_is_grounded(case.chat_ctx)

    assert "no call evidence carries the hour '09:30', though the text does" in unmatched.reasoning


async def test_a_left_over_fact_with_no_judge_model_reports_it_and_says_nobody_looked() -> None:
    """A judge nobody gave a model to must not read as a pass: it fails, naming what it found."""
    case = a_case_of(
        asked(WHEN), replied("La revisión son 45 euros.", retrieved=["Revisión: 45 €."])
    )

    unmatched = await a_judge_for(case).evaluate(chat_ctx=case.chat_ctx)

    assert unmatched.failed
    assert "no text evidence carries the price '45 euros'" in unmatched.reasoning
    assert "no judge model was given" in unmatched.reasoning


# A golden that opens at `stage: choose` seeds the patient, her appointment and her doctor, and the
# view renders them into the prompt without a tool ever running. Until 2026-09-08 that call scored
# zero on grounded — "no call evidence carries the date 'jueves'" about a date the agent read off
# its own view. The state is what the view was rendered from, and the log carries the state whole.
SEEDED = {
    "stage": "choose",
    "patient": {
        "id": "p-1041",
        "name": "Ana García",
        "phone": "+34 600 000 001",
        "cita": "jueves a las diez",
        "doctor": "la doctora Vidal",
    },
}


async def test_a_fact_the_seeded_state_carried_is_grounded_and_asks_nobody() -> None:
    """The state is evidence: the agent read the date and the doctor straight off its own view."""
    judge = CountingJudge()
    case = a_case_of(
        asked("¿Cuándo tengo la cita?"),
        replied("Tiene el jueves con la doctora Vidal."),
        states=[SEEDED],
    )

    assert await a_score(case, judge) == 1.0
    assert judge.prompts == []


async def test_a_state_the_call_was_never_in_grounds_nothing() -> None:
    """The mirror of it: a date nobody seeded and no tool answered is still a finding."""
    case = a_case_of(
        asked("¿Cuándo tengo la cita?"),
        replied("Tiene el domingo con la doctora Vidal."),
        states=[SEEDED],
    )

    unmatched = a_judge_for(case).every_fact_is_grounded(case.chat_ctx)

    assert unmatched.failed
    assert "no call evidence carries the date 'domingo'" in unmatched.reasoning
    # And the doctor of the same sentence is not reported: she IS in the state.
    assert "Vidal" not in unmatched.reasoning


async def test_the_state_reaches_the_one_question_a_judge_is_ever_asked() -> None:
    """What code could not match is asked with the whole evidence, the state's half included."""
    judge = CountingJudge(verdict="pass")
    case = a_case_of(
        asked("¿A qué hora?"),
        replied("A las 10:00 con la doctora Vidal.", retrieved=["Horario: 09:00 a 20:00."]),
        states=[SEEDED],
    )

    score = await measured(a_judge_for(case), case, judge)

    assert score.judge_calls == 1
    assert "jueves a las diez" in judge.prompts[0]


async def test_the_same_state_read_twice_is_one_block_of_evidence() -> None:
    """Every entry carries the whole state, so identical readings would repeat themselves."""
    case = a_case_of(asked("¿Cuándo?"), replied("El jueves."), states=[SEEDED, SEEDED])

    assert len(evidence_of(case).state) == 1


# From the field, 2026-09-08: the agent called `freeSlots("domingo por la mañana")`, read `[]`, said
# "no hay huecos" — and the judge, shown a bare `[]`, failed a sentence the log fully supported.
NO_SLOTS = a_call("freeSlots", {"day": "domingo por la mañana"}, [])

AS_THE_JUDGE_READS_IT = 'freeSlots({"day": "domingo por la mañana"}) → []'


async def test_a_tool_answer_reaches_the_judge_with_its_name_and_arguments() -> None:
    """An empty answer is information about the day it was asked about, and the call says which."""
    judge = CountingJudge(verdict="pass")
    case = a_case_of(
        asked("¿Tiene hueco el domingo por la mañana?"),
        replied(
            "El domingo por la mañana no hay huecos. La revisión son 45 euros.", calls=[NO_SLOTS]
        ),
    )

    score = await measured(a_judge_for(case), case, judge)

    assert score.judge_calls == 1
    assert AS_THE_JUDGE_READS_IT in judge.prompts[0]


async def test_a_day_only_the_arguments_carry_is_grounded_and_asks_nobody() -> None:
    """The agenda was asked about Sunday and answered: code matches it, and no model is reached."""
    judge = CountingJudge()
    case = a_case_of(
        asked("¿Tiene hueco el domingo?"),
        replied("El domingo no hay hueco.", calls=[a_call("freeSlots", {"day": "domingo"}, [])]),
    )

    assert await a_score(case, judge) == 1.0
    assert judge.prompts == []
