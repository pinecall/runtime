"""The persona's verdict: the caller's own rule, asked of the judge — accepted, declined, unsure."""

from __future__ import annotations

from dataclasses import replace

import pytest

from pinecall.evals.judges.persona import NOBODY_TO_ASK, PersonaJudge, persona_judge_of
from tests.evals.conversations import a_case_of, asked, replied
from tests.evals.fakes import CountingJudge

pytestmark = pytest.mark.unit

A_CALL = a_case_of(
    asked("¿Cuánto cuesta la limpieza?"), replied("Son 60 € y el viernes hay hueco.")
)


async def test_a_call_that_met_the_callers_rule_is_held_and_says_accepted() -> None:
    judge = CountingJudge(verdict="pass", reason="the price and a Friday were given")

    said = await PersonaJudge("a price and a day this week", "").evaluate(
        chat_ctx=A_CALL.chat_ctx, llm=judge
    )

    assert said.verdict == "pass"
    assert said.reasoning == "accepted: the price and a Friday were given"


async def test_a_call_that_met_what_declines_it_is_broken_and_says_declined() -> None:
    judge = CountingJudge(verdict="fail", reason="they were told to call back")

    said = await PersonaJudge("", "they are told to call back").evaluate(
        chat_ctx=A_CALL.chat_ctx, llm=judge
    )

    assert (said.verdict, said.reasoning) == ("fail", "declined: they were told to call back")


async def test_a_judge_that_cannot_tell_says_so_rather_than_picking_a_side() -> None:
    judge = CountingJudge(verdict="maybe", reason="the call ended before a price")

    said = await PersonaJudge("a price", "").evaluate(chat_ctx=A_CALL.chat_ctx, llm=judge)

    assert (said.verdict, said.reasoning) == (
        "maybe",
        "could not say: the call ended before a price",
    )


async def test_the_judge_is_asked_both_halves_and_never_one_the_caller_left_empty() -> None:
    both = CountingJudge()
    one = CountingJudge()

    await PersonaJudge("a price", "a call back").evaluate(chat_ctx=A_CALL.chat_ctx, llm=both)
    await PersonaJudge("a price", "").evaluate(chat_ctx=A_CALL.chat_ctx, llm=one)

    assert "accepts the call only if this happened on it: a price" in both.prompts[0]
    assert "declines the call if this happened on it: a call back" in both.prompts[0]
    assert "declines the call" not in one.prompts[0]


# No model, no answer: `broken` here is what score.py files as `skipped` with the ceiling's reason.
async def test_with_no_judge_model_nothing_is_settled_and_nothing_is_passed() -> None:
    said = await PersonaJudge("a price", "").evaluate(chat_ctx=A_CALL.chat_ctx, llm=None)

    assert (said.verdict, said.reasoning) == ("fail", NOBODY_TO_ASK)
    assert said.instructions


def test_only_a_call_whose_caller_wrote_a_rule_gets_this_judge() -> None:
    ruled = replace(a_case_of(asked("hola")), persona_rule=("a price", ""))

    assert persona_judge_of(a_case_of(asked("hola"))) is None
    assert isinstance(persona_judge_of(ruled), PersonaJudge)
