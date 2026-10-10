"""Tests for asking one judge: the trigger, the question's blocks, each answer read."""

import pytest

from pinecall.domain.errors import UpstreamFailed
from pinecall.domain.judging import JudgeSpec
from pinecall.evals._asking import Answered, Context, ask, conversation_of
from pinecall.evals.case import Called, Case
from tests.evals.conftest import (
    a_judge,
    agent_line,
    answering,
    caller_line,
    case_of_turns,
    logged_call,
    prompts_of,
)
from tests.fakes.acme import AcmeLLM

SLOT = JudgeSpec(name="offers-next-slot", question="Did the agent offer the next free slot?")


MOOD = JudgeSpec(
    name="mood",
    question="How did the caller feel by the end?",
    answer="choice",
    choices=("positive", "negative"),
)


POLITE = JudgeSpec(name="polite", question="How polite was the agent?", answer="score")


REFUND = JudgeSpec(
    name="refund-offered",
    question="Did the agent offer the refund the policy allows?",
    on="trigger",
    trigger="The caller asked for a refund.",
)


def offered() -> Case:
    return case_of_turns(caller_line("¿Hay hueco?"), agent_line("El viernes a las diez."))


def submitted(tool: str, **arguments: object) -> dict[str, object]:
    return {"name": tool, "arguments": {"reason": "because", **arguments}}


# ── a verdict ──


async def test_a_verdict_held_is_read_with_its_reason_and_its_positions() -> None:
    model = answering(submitted("submit_verdict", verdict="held", positions=[2, 2, 1]))
    answered = await ask(SLOT, offered(), Context(), model)
    assert (answered.verdict, answered.reason, answered.positions) == ("held", "because", (2, 1))
    assert len(answered.spent) == 1
    assert answered.is_an_eval


async def test_a_verdict_broken_is_an_eval_and_na_is_not() -> None:
    broken = await ask(SLOT, offered(), Context(), a_judge(("broken", "no slot")))
    na = await ask(SLOT, offered(), Context(), a_judge(("na", "nothing was asked")))
    assert (broken.verdict, broken.is_an_eval) == ("broken", True)
    assert (na.verdict, na.reason, na.is_an_eval) == ("na", "nothing was asked", False)


async def test_an_answer_out_of_the_schema_is_na_and_never_a_verdict() -> None:
    answered = await ask(SLOT, offered(), Context(), a_judge(("probably", "cannot tell")))
    assert answered.verdict == "na"


async def test_positions_that_are_not_numbers_are_dropped_and_the_answer_stands() -> None:
    model = answering(submitted("submit_verdict", verdict="held", positions="the second line"))
    answered = await ask(SLOT, offered(), Context(), model)
    assert (answered.verdict, answered.positions) == ("held", ())


async def test_the_answer_is_forced_through_the_one_tool_the_judge_answers_with() -> None:
    model = a_judge(("held", "ok"))
    await ask(SLOT, offered(), Context(), model)
    [request] = model.requests
    assert (request.tools, request.tool_choice) == (["submit_verdict"], "required")


async def test_a_judge_model_that_calls_no_tool_is_a_vendor_that_failed() -> None:
    model = AcmeLLM(api_key="k", replies=[["no pienso"]])
    with pytest.raises(UpstreamFailed, match="without calling submit_verdict"):
        await ask(SLOT, offered(), Context(), model)


# ── a choice and a score ──


async def test_a_choice_among_the_judges_own_is_a_classification() -> None:
    answered = await ask(
        MOOD, offered(), Context(), answering(submitted("submit_choice", choice="negative"))
    )
    assert (answered.verdict, answered.choice, answered.score) == ("classified", "negative", None)
    assert answered.is_an_eval


async def test_a_choice_the_judge_was_never_given_is_na() -> None:
    answered = await ask(
        MOOD, offered(), Context(), answering(submitted("submit_choice", choice="angry"))
    )
    assert (answered.verdict, answered.choice) == ("na", None)


async def test_a_score_from_one_to_five_is_a_classification_read_as_a_number() -> None:
    answered = await ask(
        POLITE, offered(), Context(), answering(submitted("submit_score", score="4"))
    )
    assert (answered.verdict, answered.score) == ("classified", 4)


async def test_a_score_past_five_is_na() -> None:
    answered = await ask(
        POLITE, offered(), Context(), answering(submitted("submit_score", score="7"))
    )
    assert (answered.verdict, answered.score) == ("na", None)


# ── a trigger ──


async def test_a_trigger_that_does_not_hold_is_na_and_the_question_is_never_asked() -> None:
    model = answering(
        {"name": "submit_applies", "arguments": {"applies": False, "reason": "no refund"}}
    )
    answered = await ask(REFUND, offered(), Context(), model)
    assert (answered.verdict, answered.reason) == ("na", "no refund")
    assert len(model.requests) == 1
    assert len(answered.spent) == 1
    assert "The caller asked for a refund." in prompts_of(model)[0]
    assert REFUND.question not in prompts_of(model)[0]


async def test_a_trigger_that_holds_asks_the_question_and_both_requests_are_spent() -> None:
    model = answering(
        {"name": "submit_applies", "arguments": {"applies": True, "reason": "asked"}},
        submitted("submit_verdict", verdict="broken"),
    )
    answered = await ask(REFUND, offered(), Context(), model)
    assert answered.verdict == "broken"
    assert len(model.requests) == 2
    assert len(answered.spent) == 2
    assert REFUND.question in prompts_of(model)[1]


# ── what the question carries ──


async def test_a_judge_that_reads_nothing_more_is_shown_its_question_and_the_call_alone() -> None:
    model = a_judge(("held", "ok"))
    context = Context(
        prompt="You are Sofía.", evidence="Revisión: 45 €.", facts="- direction: inbound"
    )
    await ask(SLOT, offered(), context, model)
    prompt = prompts_of(model)[0]
    assert SLOT.question in prompt
    assert "#2 agent: El viernes a las diez." in prompt
    for block in ("The agent's prompt:", "Evidence the call carried:", "Facts about this call:"):
        assert block not in prompt


async def test_each_block_a_judge_reads_reaches_it() -> None:
    reading = JudgeSpec(
        name="reads-all",
        question="q",
        reads_prompt=True,
        reads_evidence=True,
        reads_facts=True,
    )
    model = a_judge(("held", "ok"))
    context = Context(
        prompt="You are Sofía.", evidence="Revisión: 45 €.", facts="- direction: inbound"
    )
    await ask(reading, offered(), context, model)
    prompt = prompts_of(model)[0]
    assert "The agent's prompt:\nYou are Sofía." in prompt
    assert "Evidence the call carried:\nRevisión: 45 €." in prompt
    assert "Facts about this call:\n- direction: inbound" in prompt


async def test_no_evidence_or_facts_is_said_none_and_an_empty_prompt_is_left_out() -> None:
    reading = JudgeSpec(
        name="reads-all", question="q", reads_prompt=True, reads_evidence=True, reads_facts=True
    )
    model = a_judge(("held", "ok"))
    await ask(reading, offered(), Context(), model)
    prompt = prompts_of(model)[0]
    assert "The agent's prompt:" not in prompt
    assert "Evidence the call carried:\n(none)" in prompt
    assert "Facts about this call:\n(none)" in prompt


# ── the conversation ──


def test_every_line_starts_with_its_position_and_a_tool_call_says_what_it_answered() -> None:
    failed = Called(
        call_id="c2", name="book", arguments={"slot": "v10"}, answer="taken", failed=True
    )
    case = case_of_turns(
        caller_line("¿Hay hueco el viernes?"),
        agent_line(
            "Sí, a las diez.",
            seq=4,
            calls=(logged_call("find_slots", {"day": "viernes"}, "10:00"), failed),
        ),
    )
    assert conversation_of(case).split("\n") == [
        "#1 caller: ¿Hay hueco el viernes?",
        "#4 agent: Sí, a las diez.",
        '#4 [tool call: find_slots({"day": "viernes"})]',
        "#4 [tool answer: 10:00]",
        '#4 [tool call: book({"slot": "v10"})]',
        "#4 [tool error: taken]",
    ]


def test_a_skipped_or_deferred_answer_is_not_an_eval() -> None:
    assert not Answered("skipped", "no model").is_an_eval
    assert not Answered("deferred", "unsure").is_an_eval
    assert Answered("classified", "ok", choice="positive").is_an_eval
