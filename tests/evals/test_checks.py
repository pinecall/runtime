"""Tests for ring 3: a finished call checked by code alone."""

import dataclasses

from pinecall.domain.names import JsonObject
from pinecall.evals.checks import (
    DEFAULT_BUDGET,
    Failure,
    consent,
    consent_of,
    errors,
    interruptions,
    latency,
    rebuild,
    register,
    replay,
    talk,
)
from tests.evals.conftest import (
    BOOK,
    a_log,
    agent,
    before_the_yes,
    caller,
    confirmation,
    confirmed,
    result,
    tool,
    with_no_gate,
)
from tests.wire.golden import golden_entries

IRREVERSIBLE = frozenset({BOOK.name})


# Words banned by a clinic that addresses its patients as usted.
TUTEO = ("te", "tienes", "tu cita")


def test_a_booking_the_caller_said_yes_to_passes() -> None:
    verdict = consent(rebuild(confirmed()), IRREVERSIBLE)
    assert verdict.status == "held"
    assert "1 irreversible tool call(s)" in verdict.detail


def test_a_booking_that_ran_before_the_yes_fails_and_names_both_seqs() -> None:
    verdict = consent(rebuild(before_the_yes()), IRREVERSIBLE)
    assert verdict.status == "broken"
    assert verdict.detail == "book_appointment ran at seq 4, before its confirm.granted at seq 6"


def test_an_irreversible_tool_with_no_confirm_at_all_reads_deferred() -> None:
    verdict = consent(rebuild(with_no_gate()), IRREVERSIBLE)
    assert verdict.status == "deferred"
    assert "the confirmation gate is not built yet" in verdict.detail
    assert "1 irreversible tool call(s)" in verdict.detail


def test_a_call_where_nothing_irreversible_ran_passes() -> None:
    assert consent(rebuild(with_no_gate()), frozenset()).status == "held"


def test_a_call_whose_agent_declares_nothing_here_is_skipped_and_not_passed() -> None:
    verdict = consent(rebuild(with_no_gate()), None)
    assert verdict.status == "skipped"
    assert "clinica-norte" in verdict.detail


def test_a_grant_minted_for_somebody_else_is_not_the_callers_yes() -> None:
    log = a_log(
        confirmation("confirm.request", call_id="c1"),
        confirmation("confirm.granted", call_id="c1", audience="supervisor"),
        tool(BOOK.name, {}, call_id="c1"),
    )
    verdict = consent(rebuild(log), IRREVERSIBLE)
    assert verdict.status == "broken"
    assert "confirmed by supervisor and asked of caller" in verdict.detail


def test_a_booking_after_a_no_is_named_with_the_no() -> None:
    log = a_log(confirmation("confirm.declined", call_id="c1"), tool(BOOK.name, {}, call_id="c1"))
    verdict = consent(rebuild(log), IRREVERSIBLE)
    assert verdict.detail == "book_appointment ran at seq 2, after its confirm.declined at seq 1"


def test_a_yes_to_one_booking_is_not_a_yes_to_the_next() -> None:
    log = a_log(
        confirmation("confirm.granted", call_id="c1"),
        tool(BOOK.name, {}, call_id="c1"),
        confirmation("confirm.request", call_id="c2"),
        tool(BOOK.name, {}, call_id="c2"),
    )
    verdict = consent(rebuild(log), IRREVERSIBLE)
    assert verdict.detail == "book_appointment ran at seq 4 with no confirm.granted before it"


def test_a_trace_with_no_declared_side_effect_is_never_read_as_a_pass() -> None:
    assert consent_of(rebuild(confirmed()).gate).outcome == "undeclared"


def test_the_register_scan_names_the_word_and_the_turn_it_was_said_in() -> None:
    verdict = register(rebuild(before_the_yes()), TUTEO)
    assert verdict.status == "broken"
    assert verdict.detail == "the agent said 'te' in turn 1"


def test_the_register_scan_passes_a_call_that_said_none_of_the_words() -> None:
    verdict = register(rebuild(confirmed()), TUTEO)
    assert verdict.status == "held"
    assert "3 declared word(s)" in verdict.detail


def test_a_banned_word_is_a_whole_word_and_a_phrase_is_found_anywhere() -> None:
    log = a_log(agent("El tutor revisa tu cita."))
    verdict = register(rebuild(log), ("tu", "revisa tu"))
    assert verdict.detail == "the agent said 'tu' in turn 1; 'revisa tu' in turn 1"
    assert register(rebuild(log), ("tuto",)).status == "held"


def test_a_scan_with_no_declared_words_is_skipped_and_not_a_pass() -> None:
    assert register(rebuild(confirmed()), ()).status == "skipped"


def test_a_call_that_logged_nothing_broken_passes_the_error_check() -> None:
    assert errors(rebuild(confirmed())).status == "held"


def test_an_error_the_session_did_not_recover_from_fails_and_names_it() -> None:
    broke = Failure(seq=6, code="tts_unavailable", message="the voice: 502", recoverable=False)
    verdict = errors(dataclasses.replace(rebuild(confirmed()), failures=(broke,)))
    assert verdict.status == "broken"
    assert verdict.detail == "seq 6 tts_unavailable: the voice: 502"


def test_an_error_the_session_recovered_from_is_named_and_still_passes() -> None:
    log = a_log(
        caller("hola"),
        ("error", {"code": "stt_reconnected", "message": "socket reopened", "recoverable": True}),
    )
    verdict = errors(rebuild(log))
    assert verdict.status == "held"
    assert "recovered from 1 error(s)" in verdict.detail
    assert "seq 2 stt_reconnected" in verdict.detail


def test_the_latency_verdict_is_the_worst_turn_against_the_budget() -> None:
    verdict = latency(rebuild(confirmed()), DEFAULT_BUDGET)
    assert verdict.status == "held"
    assert "e2e_latency 1.080s <= 2.000s at its worst of 2 turns" in verdict.detail
    assert "llm_node_ttft 0.400s" in verdict.detail
    assert "tts_node_ttfb 0.200s" in verdict.detail


def test_a_budget_the_call_did_not_keep_fails() -> None:
    verdict = latency(rebuild(confirmed()), {"e2e_latency": 0.5})
    assert verdict.status == "broken"
    assert verdict.detail == "e2e_latency 1.080s > 0.500s at its worst of 2 turns"


def test_one_long_silence_fails_the_call_even_when_the_median_would_pass() -> None:
    log = a_log(
        timed(caller("hola"), 0.0, 1.0),
        timed(agent("buenas"), 1.5, 2.0),
        timed(caller("quiero un turno", "sp_2"), 3.0, 4.0),
        timed(agent("un momento", "sp_2"), 8.0, 9.0),
        timed(caller("gracias", "sp_3"), 10.0, 11.0),
        timed(agent("de nada", "sp_3"), 11.5, 12.0),
    )
    call = rebuild(log)
    assert call.latencies["dead_air"] == (0.5, 4.0, 0.5)
    verdict = latency(call, {"dead_air": 2.0})
    assert verdict.status == "broken"
    assert verdict.detail == "dead_air 4.000s > 2.000s at its worst of 3 turns"


def test_an_agent_that_talks_over_its_share_fails_and_none_asked_is_skipped() -> None:
    log = a_log(
        timed(caller("hola"), 0.0, 1.0),
        timed(agent("buenas, le cuento todo lo que tenemos esta semana"), 1.5, 10.5),
    )
    call = rebuild(log)
    assert call.latencies["talk_share"] == (0.9,)
    over = talk(call, {"talk_share": 0.6})
    assert (over.status, over.detail) == (
        "broken",
        "the agent spoke 90% of the talking time, > 60%",
    )
    assert talk(call, {"talk_share": 0.95}).status == "held"
    assert talk(call, DEFAULT_BUDGET).status == "skipped"
    assert latency(call, {"talk_share": 0.1}).status == "skipped", "a share is never seconds"


def test_a_call_whose_turns_measured_nothing_is_skipped() -> None:
    verdict = latency(dataclasses.replace(rebuild(confirmed()), latencies={}), DEFAULT_BUDGET)
    assert verdict.status == "skipped"
    assert "e2e_latency" in verdict.detail


def test_a_barge_in_that_took_too_long_to_obey_fails_the_latency_budget() -> None:
    call = rebuild(golden_entries())
    assert [round(seconds, 2) for seconds in call.latencies["interruption_delay"]] == [0.33]
    verdict = latency(call, {"interruption_delay": 0.2})
    assert verdict.status == "broken"
    assert verdict.detail.startswith("interruption_delay 0.331s > 0.200s")


def test_a_replay_answers_the_six_checks_in_order_on_the_default_budget() -> None:
    verdicts = replay(confirmed(), banned=(), budget={}, irreversible=IRREVERSIBLE)
    assert [(verdict.check, verdict.status) for verdict in verdicts] == [
        ("consent", "held"),
        ("register", "skipped"),
        ("errors", "held"),
        ("latency", "held"),
        ("talk", "skipped"),
        ("interruptions", "skipped"),
    ]


def cut_off(text: str) -> tuple[str, JsonObject]:
    """The agent's reply, cut off by the caller."""
    kind, data = agent(text)
    return kind, {**data, "interrupted": True}


def test_a_reply_cut_off_and_then_answered_holds() -> None:
    call = rebuild(
        a_log(
            caller("Quería una cita."),
            cut_off("Perfecto, tengo libre el jueves a las"),
            caller("Mejor por la tarde."),
            agent("Por la tarde tengo el jueves a las cinco."),
        )
    )
    verdict = interruptions(call)
    assert (verdict.check, verdict.status) == ("interruptions", "held")
    assert "each of the 1 time(s)" in verdict.detail


def test_a_reply_started_over_instead_of_answering_is_broken_naming_both_seqs() -> None:
    call = rebuild(
        a_log(
            caller("Quería una cita."),
            cut_off("Perfecto, tengo libre el jueves a las"),
            caller("Mejor por la tarde."),
            agent("Perfecto, tengo libre el jueves a las diez."),
        )
    )
    verdict = interruptions(call)
    assert verdict.status == "broken"
    assert "seq 4 started over the one cut off at seq 2" in verdict.detail


def test_words_the_agent_never_answered_are_broken_unless_the_caller_hung_up() -> None:
    lines = [caller("Hola."), cut_off("Le cuento lo que tenemos"), caller("Espere, espere.")]
    unanswered = interruptions(rebuild(a_log(*lines)))
    assert unanswered.status == "broken"
    assert "cut in at seq 3 and the agent never answered" in unanswered.detail
    ended: JsonObject = {
        "reason": "caller_hung_up",
        "ended_by": "caller",
        "ended_at": 9.0,
        "duration_s": 8.0,
    }
    hung_up = interruptions(rebuild(a_log(*lines, ("call.ended", ended))))
    assert hung_up.status == "skipped"


def test_the_golden_calls_barge_in_is_stopped_for_and_answered() -> None:
    assert interruptions(rebuild(golden_entries())).status == "held"


def test_a_cut_the_caller_said_nothing_over_is_livekits_to_resume_and_not_judged() -> None:
    resumed = rebuild(a_log(caller("Hola."), cut_off("Le cuento"), agent("Le cuento lo que hay.")))
    verdict = interruptions(resumed)
    assert verdict.status == "skipped"
    assert "said nothing it could answer" in verdict.detail


def test_the_rebuilt_call_says_what_the_agent_said_and_whose_it_is() -> None:
    call = rebuild(a_log(caller("hola"), agent("buenas"), result("x", "y", call_id="c")))
    assert (call.call, call.agent, call.said) == ("CA_8f4a2c", "clinica-norte", ("buenas",))


def timed(turn: tuple[str, JsonObject], started: float, stopped: float) -> tuple[str, JsonObject]:
    """A turn with when its speaker started and stopped speaking."""
    kind, data = turn
    metrics = data["metrics"]
    assert isinstance(metrics, dict)
    return kind, {
        **data,
        "metrics": {**metrics, "started_speaking_at": started, "stopped_speaking_at": stopped},
    }
