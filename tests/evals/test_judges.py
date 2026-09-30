"""Tests for the judges: settled by code, or by one question to a model."""

import dataclasses

import pytest
from livekit.agents.evals import EvaluationResult, JudgmentResult

from pinecall.domain.agent import PANEL_JUDGES, AgentJudge
from pinecall.domain.errors import UpstreamFailed
from pinecall.domain.names import Json, JsonObject
from pinecall.evals.case import Case, case_of
from pinecall.evals.compliance import Panel
from pinecall.evals.judges import (
    CaseJudge,
    JudgeModel,
    at_hangup,
    evidence_in,
    hangup_judges,
    judgment_of,
)
from pinecall.providers.build import Running
from pinecall.providers.catalog import Providers
from tests.conftest import configured
from tests.evals.conftest import (
    THE_CLINIC,
    WHEN,
    a_judge,
    a_log,
    agent,
    agent_line,
    before_the_yes,
    caller,
    caller_line,
    case_of_turns,
    confirmed,
    entry,
    judge_named,
    logged_call,
    prompts_of,
    score_of,
    verdict,
    with_no_gate,
)
from tests.fakes.acme import AcmeLLM

SEEDED: JsonObject = {
    "stage": "choose",
    "patient": {"name": "Ana García", "cita": "jueves a las diez", "doctor": "la doctora Vidal"},
}


def grounded(case: Case) -> CaseJudge:
    return judge_named(hangup_judges(case, ()), "grounded")


def promises(case: Case) -> CaseJudge:
    return judge_named(hangup_judges(case, ()), "promises")


# ── consent ──


async def test_a_booking_after_the_yes_holds_and_no_judge_is_asked() -> None:
    model = a_judge()
    case = case_of(confirmed(), THE_CLINIC)
    judge = judge_named(hangup_judges(case, ()), "consent")
    assert await score_of(judge, case, model) == 1.0
    assert model.requests == []


async def test_a_booking_before_the_yes_fails_naming_both_seqs_and_still_asks_nobody() -> None:
    model = a_judge()
    case = case_of(before_the_yes(), THE_CLINIC)
    result = await verdict(judge_named(hangup_judges(case, ()), "consent"), case, model)
    assert result.failed
    assert "book_appointment ran at seq 4, before its confirm.granted at seq 6" in result.reasoning
    assert model.requests == []


async def test_a_call_with_no_confirmation_anywhere_is_not_scored_against_the_agent() -> None:
    case = case_of(with_no_gate(), THE_CLINIC)
    result = await verdict(judge_named(hangup_judges(case, ()), "consent"), case)
    assert result.passed
    assert "the log carries no confirm.* at all" in result.reasoning


async def test_a_case_built_without_the_declaration_refuses_to_report_a_pass() -> None:
    case = case_of(confirmed(), None)
    result = await verdict(judge_named(hangup_judges(case, ()), "consent"), case)
    assert result.failed
    assert "not one declared side effect" in result.reasoning


async def test_the_verdict_carries_the_question_it_answered_without_paying_for_it() -> None:
    model = a_judge()
    case = case_of(confirmed(), THE_CLINIC)
    result = await verdict(judge_named(hangup_judges(case, ()), "consent"), case, model)
    assert result.instructions.startswith("Every irreversible tool call")
    assert model.requests == []


# ── grounded ──


async def test_a_call_whose_every_fact_is_in_the_evidence_scores_one_and_asks_nobody() -> None:
    model = a_judge()
    case = case_of_turns(
        caller_line(WHEN),
        agent_line(
            "Tengo libre el 13/08 a las 09:30 con la doctora Vidal.",
            calls=(logged_call("find_slots", {"day": "13/08"}, "13/08 09:30 Vidal"),),
        ),
    )
    assert await score_of(grounded(case), case, model) == 1.0
    assert model.requests == []


async def test_a_call_that_stated_no_concrete_fact_holds_without_a_judge() -> None:
    model = a_judge()
    case = case_of_turns(caller_line(WHEN), agent_line("Claro, dígame su nombre."))
    assert await score_of(grounded(case), case, model) == 1.0
    assert model.requests == []


async def test_a_price_said_in_words_reaches_the_judge() -> None:
    model = a_judge(("pass", "45 euros is the price"))
    case = case_of_turns(
        caller_line(WHEN), agent_line("La revisión son 45 euros.", retrieved=("Revisión: 45 €.",))
    )
    judge = grounded(case)
    assert await score_of(judge, case, model) == 1.0
    assert judge.calls == 1
    assert "Revisión: 45 €." in prompts_of(model)[0]


async def test_the_judge_saying_no_is_the_score_going_to_zero() -> None:
    model = a_judge(("fail", "60, not 45"))
    case = case_of_turns(
        caller_line(WHEN), agent_line("La revisión son 45 euros.", retrieved=("Revisión: 60 €.",))
    )
    assert await score_of(grounded(case), case, model) == 0.0
    assert len(model.requests) == 1


async def test_a_judge_that_is_unsure_scores_a_half_and_never_a_pass() -> None:
    model = a_judge(("maybe", "cannot tell"))
    case = case_of_turns(
        caller_line(WHEN), agent_line("La revisión son 45 euros.", retrieved=("Revisión: 60 €.",))
    )
    result = await verdict(grounded(case), case, model)
    assert (EvaluationResult(judgments={"grounded": result}).score, result.passed) == (0.5, False)


async def test_a_verdict_out_of_the_schema_is_read_as_unsure() -> None:
    model = a_judge(("probably", "cannot tell"))
    case = case_of_turns(caller_line(WHEN), agent_line("Son 45 euros."))
    assert (await verdict(grounded(case), case, model)).verdict == "maybe"


async def test_a_left_over_fact_with_no_judge_model_reports_it_and_says_nobody_looked() -> None:
    case = case_of_turns(
        caller_line(WHEN), agent_line("La revisión son 45 euros.", retrieved=("Revisión: 45 €.",))
    )
    result = await verdict(grounded(case), case)
    assert result.failed
    assert "no text evidence carries the price '45 euros'" in result.reasoning
    assert "no judge model was given" in result.reasoning


async def test_a_fact_the_seeded_state_carried_is_grounded_and_asks_nobody() -> None:
    model = a_judge()
    case = case_of_turns(
        caller_line("¿Cuándo tengo la cita?"),
        agent_line("Tiene el jueves con la doctora Vidal."),
        states=(SEEDED,),
    )
    assert await score_of(grounded(case), case, model) == 1.0
    assert model.requests == []


async def test_a_state_the_call_was_never_in_grounds_nothing() -> None:
    case = case_of_turns(
        caller_line("¿Cuándo tengo la cita?"),
        agent_line("Tiene el domingo con la doctora Vidal."),
        states=(SEEDED,),
    )
    result = await verdict(grounded(case), case)
    assert "no call evidence carries the date 'domingo'" in result.reasoning
    assert "Vidal" not in result.reasoning


async def test_the_state_reaches_the_one_question_a_judge_is_ever_asked() -> None:
    model = a_judge(("pass", "fine"))
    case = case_of_turns(
        caller_line("¿A qué hora?"),
        agent_line("A las 10:00 con la doctora Vidal.", retrieved=("Horario: 09:00 a 20:00.",)),
        states=(SEEDED,),
    )
    judge = grounded(case)
    await verdict(judge, case, model)
    assert judge.calls == 1
    assert "jueves a las diez" in prompts_of(model)[0]


async def test_a_tool_answer_reaches_the_judge_with_its_name_and_arguments() -> None:
    model = a_judge(("pass", "fine"))
    case = case_of_turns(
        caller_line("¿Tiene hueco el domingo por la mañana?"),
        agent_line(
            "El domingo por la mañana no hay huecos. La revisión son 45 euros.",
            calls=(logged_call("freeSlots", {"day": "domingo por la mañana"}, "[]"),),
        ),
    )
    await verdict(grounded(case), case, model)
    assert 'freeSlots({"day": "domingo por la mañana"}) → []' in prompts_of(model)[0]


async def test_a_day_only_the_arguments_carry_is_grounded_and_asks_nobody() -> None:
    model = a_judge()
    case = case_of_turns(
        caller_line("¿Tiene hueco el domingo?"),
        agent_line(
            "El domingo no hay hueco.", calls=(logged_call("freeSlots", {"day": "domingo"}, "[]"),)
        ),
    )
    assert await score_of(grounded(case), case, model) == 1.0
    assert model.requests == []


async def test_a_judge_model_that_calls_no_verdict_is_a_vendor_that_failed() -> None:
    model = AcmeLLM(api_key="k", replies=[["no pienso"]])
    case = case_of_turns(caller_line(WHEN), agent_line("Son 45 euros."))
    with pytest.raises(UpstreamFailed):
        await verdict(grounded(case), case, model)


# ── promises ──


async def test_a_call_that_promises_nothing_holds_and_asks_nobody() -> None:
    model = a_judge(("fail", "never asked"))
    case = case_of_turns(caller_line("¿Abren el sábado?"), agent_line("Sí, de nueve a dos."))
    assert await score_of(promises(case), case, model) == 1.0
    assert model.requests == []


async def test_a_callback_nobody_booked_reaches_the_judge_with_every_tool_call() -> None:
    model = a_judge(("fail", "promised a call back and no tool booked one"))
    case = case_of_turns(
        caller_line("No me va bien ahora."),
        agent_line(
            "Sin problema, le llamaremos mañana por la mañana.",
            calls=(logged_call("lookupClient", {"phone": "600"}, '{"name": "Ana"}'),),
        ),
    )
    judge = promises(case)
    assert await score_of(judge, case, model) == 0.0
    assert judge.calls == 1
    assert 'lookupClient({"phone": "600"})' in prompts_of(model)[0]


# ── the caller's rule ──


def ruled(accepts: str, declines: str) -> Case:
    case = case_of_turns(
        caller_line("¿Cuánto cuesta la limpieza?"), agent_line("Son 60 € y el viernes hay hueco.")
    )
    return dataclasses.replace(case, persona_rule=(accepts, declines))


async def persona_verdict(case: Case, model: AcmeLLM | None) -> JudgmentResult:
    return await verdict(judge_named(hangup_judges(case, ()), "persona"), case, model)


async def test_a_call_that_met_the_callers_rule_is_held_and_says_accepted() -> None:
    model = a_judge(("pass", "the price and a Friday were given"))
    result = await persona_verdict(ruled("a price and a day this week", ""), model)
    assert (result.verdict, result.reasoning) == (
        "pass",
        "accepted: the price and a Friday were given",
    )


async def test_a_call_that_met_what_declines_it_is_broken_and_says_declined() -> None:
    model = a_judge(("fail", "they were told to call back"))
    result = await persona_verdict(ruled("", "they are told to call back"), model)
    assert (result.verdict, result.reasoning) == ("fail", "declined: they were told to call back")


async def test_a_judge_that_cannot_tell_says_so_rather_than_picking_a_side() -> None:
    model = a_judge(("maybe", "the call ended before a price"))
    result = await persona_verdict(ruled("a price", ""), model)
    assert result.reasoning == "could not say: the call ended before a price"


async def test_the_judge_is_asked_both_halves_and_never_one_the_caller_left_empty() -> None:
    both, one_half = a_judge(("pass", "ok")), a_judge(("pass", "ok"))
    await persona_verdict(ruled("a price", "a call back"), both)
    await persona_verdict(ruled("a price", ""), one_half)
    assert "accepts the call only if this happened on it: a price" in prompts_of(both)[0]
    assert "declines the call if this happened on it: a call back" in prompts_of(both)[0]
    assert "declines the call" not in prompts_of(one_half)[0]


async def test_the_judge_is_shown_every_tool_the_agent_called_and_what_it_answered() -> None:
    model = a_judge(("pass", "ok"))
    case = case_of_turns(
        caller_line("Quiero cita el viernes"),
        agent_line(
            "Hecho, viernes a las 10.", calls=(logged_call("book", {"day": "viernes"}, "#12"),)
        ),
    )
    await persona_verdict(dataclasses.replace(case, persona_rule=("a booking", "")), model)
    prompt = prompts_of(model)[0]
    assert (
        'assistant: Hecho, viernes a las 10.\n[function call: book({"day": "viernes"})]' in prompt
    )
    assert "[function output: #12]" in prompt


async def test_with_no_judge_model_nothing_is_settled_and_nothing_is_passed() -> None:
    result = await persona_verdict(ruled("a price", ""), None)
    assert result.failed
    assert result.instructions


def test_only_a_call_whose_caller_wrote_a_rule_gets_this_judge() -> None:
    plain = case_of_turns(caller_line("hola"))
    assert [judge.name for judge in hangup_judges(plain, ())] == ["consent", "grounded", "promises"]
    assert [judge.name for judge in hangup_judges(ruled("a price", ""), ())][-1] == "persona"


# ── the agent's own ──


SLOT = AgentJudge(name="offers-next-slot", question="The agent offered the next free slot.")


REHEARSED = AgentJudge(
    name="names-the-doctor", question="The agent named the doctor.", runs_on="simulations"
)


def offered() -> Case:
    return case_of_turns(caller_line("¿Hay hueco?"), agent_line("El viernes a las diez."))


async def own_verdict(case: Case, judge: AgentJudge, model: AcmeLLM | None) -> JudgmentResult:
    return await verdict(judge_named(hangup_judges(case, (judge,)), judge.name), case, model)


async def test_a_question_the_call_answered_holds_under_the_judges_own_name() -> None:
    model = a_judge(("pass", "the Friday slot was offered"))
    result = await own_verdict(offered(), SLOT, model)
    assert (result.verdict, result.reasoning) == ("pass", "the Friday slot was offered")
    assert judgment_of(SLOT.name, result, []).verdict == "held"


async def test_a_question_the_call_did_not_answer_is_broken_and_says_why() -> None:
    model = a_judge(("fail", "no slot was offered"))
    result = await own_verdict(offered(), SLOT, model)
    assert judgment_of(SLOT.name, result, []).verdict == "broken"
    assert result.reasoning == "no slot was offered"


async def test_the_model_is_asked_the_orgs_own_words_and_shown_the_call() -> None:
    model = a_judge(("pass", "ok"))
    result = await own_verdict(offered(), SLOT, model)
    prompt = prompts_of(model)[0]
    assert SLOT.question in prompt
    assert "assistant: El viernes a las diez." in prompt
    assert result.instructions.startswith(SLOT.question)


async def test_with_no_judge_model_the_question_is_never_passed() -> None:
    result = await own_verdict(offered(), SLOT, None)
    assert result.failed


def test_the_agents_own_judges_follow_the_panel_in_the_order_they_were_given() -> None:
    other = AgentJudge(name="says-the-price", question="The agent said the price.")
    names = [judge.name for judge in hangup_judges(offered(), (SLOT, other))]
    assert names == ["consent", "grounded", "promises", SLOT.name, other.name]


def test_a_judge_for_simulations_reads_a_simulated_call_and_never_a_real_one() -> None:
    real, simulated = offered(), dataclasses.replace(offered(), simulated=True)
    assert REHEARSED.name not in [judge.name for judge in hangup_judges(real, (REHEARSED,))]
    assert REHEARSED.name in [judge.name for judge in hangup_judges(simulated, (REHEARSED,))]
    assert SLOT.name in [judge.name for judge in hangup_judges(real, (SLOT,))]


async def test_at_hang_up_the_agents_own_judge_answers_in_call_score_and_in_the_panel(
    acme: str,
) -> None:
    replies: list[Json] = [
        [{"name": "submit_verdict", "arguments": {"verdict": "fail", "reasoning": "none"}}]
    ]
    log = a_log(caller("¿Hay hueco?"), agent("Llame mañana."))
    judge = Running(vendor=acme, credentials="k", model="acme-1", options={"replies": replies})
    score = await at_hangup(
        log, THE_CLINIC, Panel(own=(SLOT,)), JudgeModel(judge, 0.01), configured=configured()
    )
    assert score.panel == ["consent", "grounded", "promises", SLOT.name]
    own = next(judgment for judgment in score.judges if judgment.name == SLOT.name)
    assert (own.verdict, own.reason) == ("broken", "none")
    assert score.passed is False


# ── what call.score carries ──


def test_the_seqs_a_reason_names_become_the_entries_a_reader_opens() -> None:
    grant = entry(93, "confirm.granted", {"said": "Sí, confirmo."})
    evidence = evidence_in("book_slot ran at seq 79, before its confirm.granted at seq 93", [grant])
    assert evidence.seqs == [79, 93]
    assert evidence.said == "Sí, confirmo."


def test_a_reason_that_cites_nothing_carries_no_evidence_and_no_words() -> None:
    evidence = evidence_in("no irreversible tool ran in this call", [])
    assert evidence.seqs == []
    assert evidence.said is None


def test_a_judgment_keeps_the_question_it_answered_beside_the_answer() -> None:
    result = JudgmentResult(verdict="fail", reasoning="the tool ran at seq 79")
    result.instructions = "Every irreversible tool call ran after a confirm.granted."
    row = judgment_of("consent", result, [])
    assert (row.name, row.verdict) == ("consent", "broken")
    assert (row.criteria, row.reason) == (result.instructions, result.reasoning)


def with_a_judge(replies: list[Json]) -> Providers:
    return Providers.model_validate(
        {
            **configured().model_dump(mode="json"),
            "tuning": {"llm/acme": {"options": {"replies": replies}}},
            "judge": {"llm": {"vendor": "acme", "model": "acme-1"}, "ceiling_usd": 0.01},
        }
    )


async def test_at_hang_up_with_no_model_the_code_judges_answer_and_the_model_ones_are_skipped() -> (
    None
):
    log = a_log(caller("¿Cuánto?"), agent("Son 45 euros."))
    score = await at_hangup(
        log, THE_CLINIC, Panel(), JudgeModel(None, 0.0, "no judge"), configured=configured()
    )
    verdicts = {judgment.name: judgment.verdict for judgment in score.judges}
    assert verdicts == {"consent": "held", "grounded": "skipped", "promises": "held"}
    assert score.passed is True
    assert score.panel == ["consent", "grounded", "promises"]
    assert score.judge_calls == 0
    grounded_row = next(judgment for judgment in score.judges if judgment.name == "grounded")
    assert grounded_row.reason.startswith("no judge: ")
    assert score.judged_by is not None
    assert (score.judged_by.provider, score.judged_by.model) == (None, None)


async def test_at_hang_up_the_model_is_asked_counted_and_priced(acme: str) -> None:
    replies: list[Json] = [
        [{"name": "submit_verdict", "arguments": {"verdict": "pass", "reasoning": "fine"}}]
    ]
    log = a_log(caller("¿Cuánto?"), agent("Son 45 euros."))
    judge = Running(vendor=acme, credentials="k", model="acme-1", options={"replies": replies})
    score = await at_hangup(
        log, THE_CLINIC, Panel(), JudgeModel(judge, 0.01), configured=with_a_judge(replies)
    )
    assert {judgment.name: judgment.verdict for judgment in score.judges}["grounded"] == "held"
    assert score.judge_calls == 1
    assert score.judge_cost_usd is not None
    assert score.judge_cost_usd > 0
    assert score.judged_by is not None
    assert (score.judged_by.provider, score.judged_by.model) == (acme, "acme-1")
    again = await at_hangup(
        log, THE_CLINIC, Panel(), JudgeModel(judge, 0.01), configured=with_a_judge(replies)
    )
    assert again.judged_by == score.judged_by


async def test_a_model_judge_asked_past_the_ceiling_is_skipped_and_the_ones_before_it_stand(
    acme: str,
) -> None:
    replies: list[Json] = [
        [{"name": "submit_verdict", "arguments": {"verdict": "pass", "reasoning": "fine"}}]
    ] * 3
    log = a_log(caller("¿Cuánto?"), agent("Son 45 euros."))
    judge = Running(vendor=acme, credentials="k", model="acme-1", options={"replies": replies})
    other = AgentJudge(name="says-the-price", question="The agent said the price.")
    tight = JudgeModel(judge, 0.000001)
    score = await at_hangup(
        log, THE_CLINIC, Panel(own=(SLOT, other)), tight, configured=with_a_judge(replies)
    )
    verdicts = {judgment.name: judgment.verdict for judgment in score.judges}
    assert (verdicts["grounded"], verdicts[SLOT.name], verdicts[other.name]) == (
        "held",
        "skipped",
        "skipped",
    )
    skipped = next(judgment for judgment in score.judges if judgment.name == SLOT.name)
    assert skipped.reason.startswith("judging this call reached its ceiling of $1e-06")
    assert score.judge_calls == 1


def test_the_panel_gives_its_verdicts_the_names_a_judge_of_ones_own_may_not_take() -> None:
    names = {judge.name for judge in hangup_judges(offered(), ())}
    assert names <= set(PANEL_JUDGES)
    assert "persona" in PANEL_JUDGES


async def test_at_hang_up_a_judge_whose_model_failed_is_skipped_and_the_call_still_scored(
    acme: str,
) -> None:
    log = a_log(caller("¿Cuánto?"), agent("Son 45 euros."))
    judge = Running(vendor=acme, credentials="k", model="acme-1", options={"replies": [["nada"]]})
    score = await at_hangup(
        log, THE_CLINIC, Panel(), JudgeModel(judge, 0.01), configured=configured()
    )
    grounded_row = next(judgment for judgment in score.judges if judgment.name == "grounded")
    assert grounded_row.verdict == "skipped"
    assert "the judge model failed" in grounded_row.reason
    assert score.passed is True
