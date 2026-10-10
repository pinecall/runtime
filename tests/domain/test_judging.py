"""Tests for a judge's spec: its name, question, answer, choices and trigger."""

import pytest

from pinecall.domain.errors import DeclarationRefused
from pinecall.domain.judging import JudgeAnswer, JudgeSpec

QUESTION = "Did the agent offer the next free slot?"


def test_a_judge_asks_its_question_as_a_verdict_on_every_call_by_default() -> None:
    judge = JudgeSpec(name="offers-next-slot", question=QUESTION)
    assert (judge.answer, judge.on, judge.choices, judge.reads) == ("verdict", "always", (), ())


# The name is what call.score names the answer with.
@pytest.mark.parametrize("name", ["Next Slot", "next_slot", "next--slot", "-next", ""])
def test_a_name_that_is_not_lower_case_words_joined_by_hyphens_is_refused(name: str) -> None:
    with pytest.raises(DeclarationRefused, match="lower-case words joined by hyphens"):
        JudgeSpec(name=name, question=QUESTION)


def test_a_judge_with_no_question_is_refused() -> None:
    with pytest.raises(DeclarationRefused, match="write one"):
        JudgeSpec(name="blank", question="   ")


def test_a_choice_needs_two_choices_at_least() -> None:
    with pytest.raises(DeclarationRefused, match="at least two choices"):
        JudgeSpec(
            name="mood", question="How did the caller feel?", answer="choice", choices=("ok",)
        )
    two = JudgeSpec(
        name="mood", question="How did the caller feel?", answer="choice", choices=("ok", "bad")
    )
    assert two.choices == ("ok", "bad")


@pytest.mark.parametrize("answer", ["verdict", "score"])
def test_only_a_choice_takes_choices(answer: JudgeAnswer) -> None:
    with pytest.raises(DeclarationRefused, match="only a judge that answers one of several"):
        JudgeSpec(name="mood", question=QUESTION, answer=answer, choices=("a", "b"))


def test_a_judge_on_a_trigger_needs_the_triggers_question() -> None:
    with pytest.raises(DeclarationRefused, match="the trigger's question"):
        JudgeSpec(name="refund", question=QUESTION, on="trigger", trigger=" ")
    judge = JudgeSpec(
        name="refund", question=QUESTION, on="trigger", trigger="The caller asked for a refund."
    )
    assert judge.trigger == "The caller asked for a refund."


def test_what_a_judge_reads_is_named_as_the_wire_and_the_store_name_it() -> None:
    judge = JudgeSpec(
        name="grounded-prices", question=QUESTION, reads_prompt=True, reads_facts=True
    )
    assert judge.reads == ("prompt", "facts")
    everything = JudgeSpec(
        name="all", question=QUESTION, reads_prompt=True, reads_evidence=True, reads_facts=True
    )
    assert everything.reads == ("prompt", "evidence", "facts")
