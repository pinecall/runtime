"""Tests for the hang-up panel over a finished call, and the judges a golden is scored with."""

import dataclasses

from livekit.agents.evals import JudgmentResult

from pinecall.domain.judging import JudgeSpec
from pinecall.domain.names import Json
from pinecall.evals._asking import Context
from pinecall.evals.case import Case
from pinecall.evals.catalog import Seated, library
from pinecall.evals.judges import (
    AskedJudge,
    CaseJudge,
    JudgeModel,
    Panel,
    alone,
    asked_judges,
    at_hangup,
    evidence_at,
)
from pinecall.providers.build import Running
from pinecall.providers.catalog import Providers
from pinecall.wire.frames import Entry
from pinecall.wire.scores import CallScore
from tests.conftest import configured
from tests.evals.conftest import (
    THE_CLINIC,
    a_judge,
    a_log,
    agent,
    agent_line,
    answering,
    caller,
    caller_line,
    case_of_turns,
    entry,
    verdict,
)

SLOT = JudgeSpec(name="offers-next-slot", question="Did the agent offer the next free slot?")


PRICE = JudgeSpec(name="says-the-price", question="Did the agent say the price?")


MOOD = JudgeSpec(
    name="mood", question="How did the caller feel?", answer="choice", choices=("good", "bad")
)


REHEARSED = JudgeSpec(name="names-the-doctor", question="Was the doctor named?", on="simulations")


REFUND = JudgeSpec(
    name="refund-offered",
    question="Did the agent offer the refund?",
    on="trigger",
    trigger="The caller asked for a refund.",
)


def only(*names: str) -> dict[str, bool]:
    """Switches that leave on only the library's judges named."""
    return {name: name in names for name in library()}


def answered(given: str, reason: str = "because", positions: list[int] | None = None) -> Json:
    """One scripted reply: the judge's verdict, its reason and the positions it rests on."""
    seqs: list[Json] = list(positions or [])
    arguments: Json = {"verdict": given, "reason": reason, "positions": seqs}
    return [{"name": "submit_verdict", "arguments": arguments}]


def judge_model_of(acme: str, replies: list[Json]) -> Running:
    return Running(vendor=acme, credentials="k", model="acme-1", options={"replies": replies})


def priced(replies: list[Json]) -> Providers:
    return Providers.model_validate(
        {
            **configured().model_dump(mode="json"),
            "tuning": {"llm/acme": {"options": {"replies": replies}}},
            "judge": {"llm": {"vendor": "acme", "model": "acme-1"}, "ceiling_usd": 0.01},
        }
    )


def a_call() -> list[Entry]:
    return a_log(caller("¿Cuánto cuesta?"), agent("Son 45 euros."))


async def judged(panel: Panel, judge: JudgeModel, replies: list[Json] | None = None) -> CallScore:
    return await at_hangup(a_call(), THE_CLINIC, panel, judge, configured=priced(replies or []))


def verdicts_of(score: CallScore) -> dict[str, str]:
    return {judgment.name: judgment.verdict for judgment in score.judges}


def offered() -> Case:
    return case_of_turns(caller_line("¿Hay hueco?"), agent_line("El viernes a las diez."))


# ── at hang-up ──


async def test_a_held_verdict_passes_the_call_and_is_one_eval_counted_and_priced(
    acme: str,
) -> None:
    replies = [answered("held", "the price was in the tool's answer")]
    model = JudgeModel(judge_model_of(acme, replies), 0.01)
    score = await judged(Panel(switches=only("grounded")), model, replies)
    assert verdicts_of(score) == {"grounded": "held"}
    assert (score.passed, score.evals, score.judge_calls) == (True, 1, 1)
    assert score.panel == ["grounded"]
    assert score.judge_cost_usd is not None
    assert score.judge_cost_usd > 0
    assert score.judged_by is not None
    assert (score.judged_by.provider, score.judged_by.model) == (acme, "acme-1")


async def test_the_same_questions_hash_the_same_whatever_the_call_said(acme: str) -> None:
    first = await judged(
        Panel(switches=only("grounded")), JudgeModel(judge_model_of(acme, [answered("held")]), 0.01)
    )
    second = await judged(
        Panel(switches=only("grounded")),
        JudgeModel(judge_model_of(acme, [answered("broken")]), 0.01),
    )
    assert first.judged_by == second.judged_by


async def test_a_broken_verdict_fails_the_call_and_carries_the_lines_it_rests_on(
    acme: str,
) -> None:
    replies = [answered("broken", "45 is nowhere", positions=[2, 9])]
    score = await judged(
        Panel(switches=only("grounded")), JudgeModel(judge_model_of(acme, replies), 0.01)
    )
    [judgment] = score.judges
    assert (judgment.verdict, judgment.reason, score.passed) == ("broken", "45 is nowhere", False)
    assert judgment.evidence.seqs == [2]
    assert judgment.evidence.said == "Son 45 euros."
    assert judgment.criteria == library()["grounded"].spec.question


async def test_a_gate_settles_na_before_any_model_is_asked_and_bills_nothing(acme: str) -> None:
    score = await judged(
        Panel(switches=only("consent")), JudgeModel(judge_model_of(acme, []), 0.01)
    )
    [judgment] = score.judges
    assert (judgment.verdict, judgment.reason) == (
        "na",
        "no tool this agent declares irreversible ran on this call",
    )
    assert (score.passed, score.not_judged, score.evals, score.judge_calls) == (None, None, 0, 0)


async def test_with_no_model_every_judge_is_skipped_saying_why() -> None:
    score = await judged(Panel(switches=only("grounded")), JudgeModel(None, 0.0, "no judge here"))
    assert verdicts_of(score) == {"grounded": "skipped"}
    assert (score.passed, score.not_judged, score.judge_calls) == (None, "no judge here", 0)
    assert score.judged_by is not None
    assert (score.judged_by.provider, score.judged_by.model) == (None, None)


async def test_a_judge_whose_model_failed_is_skipped_and_the_call_still_scored(acme: str) -> None:
    replies: list[Json] = [["nada"]]
    score = await judged(
        Panel(switches=only("grounded")), JudgeModel(judge_model_of(acme, replies), 0.01)
    )
    [judgment] = score.judges
    assert judgment.verdict == "skipped"
    assert "the judge model failed" in judgment.reason
    assert score.not_judged == "every judge run over this call failed and not one of them answered"


async def test_a_judge_asked_past_the_ceiling_is_skipped_and_the_ones_before_it_stand(
    acme: str,
) -> None:
    replies = [answered("held")] * 2
    tight = JudgeModel(judge_model_of(acme, replies), 0.000001)
    score = await judged(Panel(switches=only(), own=(SLOT, PRICE)), tight, replies)
    assert verdicts_of(score) == {SLOT.name: "held", PRICE.name: "skipped"}
    assert score.judges[1].reason.startswith("judging this call reached its ceiling of $1e-06")
    assert (score.judge_calls, score.evals, score.passed) == (1, 1, True)


async def test_once_the_orgs_evals_are_used_up_the_judges_after_are_not_asked(acme: str) -> None:
    replies = [answered("held")] * 2
    one_left = JudgeModel(judge_model_of(acme, replies), 0.01, evals_left=1)
    score = await judged(Panel(switches=only(), own=(SLOT, PRICE)), one_left, replies)
    assert verdicts_of(score) == {SLOT.name: "held", PRICE.name: "skipped"}
    assert score.judges[1].reason.startswith("the org's evals for the month are used up")
    none_left = JudgeModel(judge_model_of(acme, replies), 0.01, evals_left=0)
    nothing = await judged(Panel(switches=only(), own=(SLOT,)), none_left, replies)
    assert (verdicts_of(nothing), nothing.judge_calls) == ({SLOT.name: "skipped"}, 0)


async def test_a_classification_is_an_eval_and_never_passes_or_fails_the_call(acme: str) -> None:
    replies: list[Json] = [
        [{"name": "submit_choice", "arguments": {"choice": "bad", "reason": "r"}}]
    ]
    score = await judged(
        Panel(switches=only(), own=(MOOD,)),
        JudgeModel(judge_model_of(acme, replies), 0.01),
        replies,
    )
    [judgment] = score.judges
    assert (judgment.verdict, judgment.choice) == ("classified", "bad")
    assert (score.passed, score.not_judged, score.evals) == (None, None, 1)


async def test_a_trigger_that_did_not_hold_is_na_its_request_counted_and_no_eval(
    acme: str,
) -> None:
    replies: list[Json] = [
        [{"name": "submit_applies", "arguments": {"applies": False, "reason": "no refund"}}]
    ]
    score = await judged(
        Panel(switches=only(), own=(REFUND,)),
        JudgeModel(judge_model_of(acme, replies), 0.01),
        replies,
    )
    assert verdicts_of(score) == {REFUND.name: "na"}
    assert (score.judge_calls, score.evals) == (1, 0)


async def test_the_library_switched_on_comes_first_then_the_orgs_own(acme: str) -> None:
    replies = [answered("held")] * 3
    score = await judged(
        Panel(switches=only("grounded"), own=(SLOT, REHEARSED)),
        JudgeModel(judge_model_of(acme, replies), 0.01),
        replies,
    )
    assert score.panel == ["grounded", SLOT.name], "a judge for simulations meets no real call"


# ── a golden's judges ──


def test_a_golden_may_name_any_judge_of_the_library_or_of_the_orgs_own() -> None:
    by_name = asked_judges(offered(), Panel(switches=only(), own=(SLOT,)))
    assert set(by_name) == {*library(), SLOT.name}


def asked_of(spec: JudgeSpec, case: Case | None = None) -> AskedJudge:
    return AskedJudge(Seated(spec), case or offered(), Context())


async def test_a_judge_asked_of_a_golden_fails_only_on_broken() -> None:
    broken = await verdict(asked_of(SLOT), offered(), a_judge(("broken", "no slot")))
    holding = await verdict(asked_of(SLOT), offered(), a_judge(("held", "the Friday")))
    na = await verdict(asked_of(SLOT), offered(), a_judge(("na", "nothing asked")))
    assert [result.verdict for result in (broken, holding, na)] == ["fail", "pass", "pass"]
    assert (broken.reasoning, broken.instructions) == ("no slot", SLOT.question)


async def test_a_golden_judge_counts_every_request_it_made() -> None:
    model = answering(
        {"name": "submit_applies", "arguments": {"applies": True, "reason": "asked"}},
        {"name": "submit_verdict", "arguments": {"verdict": "held", "reason": "ok"}},
    )
    judge = asked_of(REFUND)
    await verdict(judge, offered(), model)
    assert judge.calls == 2


async def test_a_golden_judge_its_gate_settles_passes_without_a_model() -> None:
    consent = library()["consent"]
    judge = AskedJudge(Seated(consent.spec, consent.gate), offered(), Context())
    result = await verdict(judge, offered())
    assert result.passed
    assert judge.calls == 0


async def test_a_golden_judge_with_no_model_fails_saying_so() -> None:
    result = await verdict(asked_of(SLOT), offered())
    assert result.failed
    assert "needs the judge model" in result.reasoning


async def test_an_expectation_settled_by_code_answers_what_code_settled() -> None:
    settled = JudgmentResult(verdict="fail", reasoning="heard 1 of 2")
    result = await verdict(CaseJudge("heard", "every line was heard", settled), offered())
    assert (result.verdict, result.reasoning, result.instructions) == (
        "fail",
        "heard 1 of 2",
        "every line was heard",
    )


# ── one judge alone ──


def test_one_of_the_librarys_alone_is_its_switch_on_and_every_other_off() -> None:
    panel = alone(library()["sentiment"].spec, Panel(switches={"grounded": True}, own=(SLOT,)))
    assert {name for name, on in panel.switches.items() if on} == {"sentiment"}
    assert panel.own == []


def test_a_judge_of_ones_own_alone_is_the_whole_library_off() -> None:
    panel = alone(PRICE, Panel(own=(SLOT,), prompt="You are Sofía."))
    assert not any(panel.switches.values())
    assert (panel.own, panel.prompt) == ([PRICE], "You are Sofía.")


def test_a_question_rewritten_under_a_librarys_name_is_asked_as_written() -> None:
    rewritten = dataclasses.replace(library()["sentiment"].spec, question="Was the caller calm?")
    panel = alone(rewritten, Panel())
    assert not any(panel.switches.values())
    assert panel.own == [rewritten]


# ── the lines an answer rests on ──


def test_the_positions_an_answer_names_become_the_entries_a_reader_opens() -> None:
    log = [entry(4, "tool.call", {}), entry(6, "turn.agent", {"text": "Hecho."})]
    evidence = evidence_at([9, 4, 6], log)
    assert (evidence.seqs, evidence.said) == ([4, 6], "Hecho.")


def test_an_answer_that_names_no_line_carries_no_evidence_and_no_words() -> None:
    evidence = evidence_at([], a_call())
    assert (evidence.seqs, evidence.said) == ([], None)


def test_a_simulated_call_is_judged_by_what_simulations_run() -> None:
    simulated = dataclasses.replace(offered(), simulated=True)
    assert REHEARSED.name in asked_judges(simulated, Panel(own=(REHEARSED,)))
