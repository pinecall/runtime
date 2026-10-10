"""Tests for a finished call as a judge reads it."""

from typing import get_args

from livekit.agents.evals import Verdict

from pinecall.domain.agent import AgentConfig
from pinecall.domain.names import JsonObject
from pinecall.evals.case import VERDICT_WORDS, as_chat, case_of, verdict_word, words_of
from pinecall.wire.parts import ScoreVerdict
from tests.evals.conftest import (
    A_SUMMARY,
    BOOK,
    THE_CLINIC,
    a_log,
    agent,
    caller,
    confirmation,
    confirmed,
    entry,
    result,
    tool,
)

LLM_BLOCK: JsonObject = {
    "type": "llm_metrics",
    "label": "acme.LLM",
    "request_id": "r1",
    "timestamp": 1.0,
    "duration": 0.9,
    "ttft": 0.5,
    "cancelled": False,
    "completion_tokens": 5,
    "prompt_tokens": 20,
    "prompt_cached_tokens": 0,
    "total_tokens": 25,
    "tokens_per_second": 5.0,
    "speech_id": "sp_1",
}


def test_the_turns_come_out_in_the_order_the_log_wrote_them() -> None:
    turns = case_of(confirmed(), THE_CLINIC).turns
    assert [turn.role for turn in turns] == ["user", "assistant", "user", "assistant"]
    assert [turn.seq for turn in turns] == [1, 2, 4, 8]


def test_a_tool_call_carries_its_arguments_its_contract_and_what_the_model_read_back() -> None:
    [booking] = [called for turn in case_of(confirmed(), THE_CLINIC).turns for called in turn.calls]
    assert booking.arguments == {"slot": "jueves 10:00"}
    assert "BK-5521" in (booking.answer or "")
    assert not booking.failed
    assert booking.description == BOOK.description


def test_a_tool_the_app_never_answered_is_told_apart_from_an_empty_answer() -> None:
    log = a_log(caller("hola"), tool("find_slots", {}, call_id="c1"), agent("un momento"))
    [called] = case_of(log, THE_CLINIC).turns[1].calls
    assert called.answer is None


def test_a_turn_keeps_livekits_own_metric_names_and_the_blocks_beside_them() -> None:
    log = [
        *a_log(caller("hola"), agent("buenas", e2e_latency=1.72, llm_node_ttft=0.51)),
        entry(3, "metrics.llm", LLM_BLOCK),
    ]
    spoke = case_of(log, THE_CLINIC).turns[1]
    assert spoke.metrics["e2e_latency"] == 1.72
    assert spoke.metrics["llm_node_ttft"] == 0.51
    assert [block["type"] for block in spoke.blocks] == ["llm_metrics"]


def test_what_retrieval_put_in_front_of_the_model_travels_with_the_turn_it_was_for() -> None:
    sources: JsonObject = {
        "query": "precio",
        "took_ms": 3.0,
        "speech_id": "sp_1",
        "sources": [
            {
                "id": "c1",
                "path": "tarifas.md",
                "heading": "Tarifas",
                "score": 0.9,
                "excerpt": "45 €",
            }
        ],
    }
    log = a_log(caller("¿cuánto cuesta?"), ("docs.sources", sources), agent("Son 45 euros."))
    case = case_of(log, THE_CLINIC)
    assert case.turns[1].retrieved == ("Tarifas\n45 €",)
    assert case.turns[0].retrieved == ()


def test_the_gate_trace_is_in_seq_order_and_says_what_each_tool_does() -> None:
    gate = case_of(confirmed(), THE_CLINIC).gate
    assert [(line.kind, line.tool, line.seq) for line in gate] == [
        ("confirm.request", BOOK.name, 3),
        ("confirm.granted", BOOK.name, 5),
        ("tool.call", BOOK.name, 6),
    ]
    assert [line.side_effect for line in gate if line.kind == "tool.call"] == ["irreversible"]


def test_a_case_built_with_no_declaration_knows_no_side_effect() -> None:
    gate = case_of(confirmed(), None).gate
    assert [line.side_effect for line in gate if line.kind == "tool.call"] == [None]


def test_the_declaration_travels_whole_so_a_judge_can_read_what_was_promised() -> None:
    declared = case_of(confirmed(), THE_CLINIC).declared
    assert declared is not None
    assert declared.tools_by_name[BOOK.name].confirm == BOOK.confirm


def test_the_calls_own_summary_lands_verbatim_so_nothing_recomputes_a_cost() -> None:
    log = [*confirmed(), entry(9, "call.summary", A_SUMMARY)]
    summary = case_of(log, THE_CLINIC).summary
    assert summary is not None
    assert summary["outcome"] == "booked"
    assert summary["cost"] == A_SUMMARY["cost"]


def test_every_fact_that_arrived_lands_on_the_case_with_the_seq_that_joins_it_to_a_turn() -> None:
    fact: JsonObject = {"name": "slot_freed", "data": {"at": "10:15"}, "source": "app"}
    log = a_log(
        caller("hola"),
        agent("buenas"),
        ("event.received", fact),
        agent("Se liberó a las 10:15.", "sp_2"),
    )
    case = case_of(log, THE_CLINIC)
    assert [(item.name, item.data, item.source, item.seq) for item in case.arrived] == [
        ("slot_freed", {"at": "10:15"}, "app", 3)
    ]
    assert [turn.text for turn in case.turns if turn.seq > case.arrived[0].seq] == [
        "Se liberó a las 10:15."
    ]


def test_the_case_hands_livekit_the_conversation_with_every_call_and_its_answer() -> None:
    items = as_chat(case_of(confirmed(), THE_CLINIC)).items
    assert [item.type for item in items] == [
        "message",
        "message",
        "message",
        "message",
        "function_call",
        "function_call_output",
    ]


def test_every_state_the_call_was_in_comes_out_whole_and_in_order() -> None:
    log = a_log(
        ("state.changed", {"state": {"stage": "greeting"}, "changed": ["stage"]}),
        caller("hola"),
        ("state.changed", {"state": {"stage": "booked", "booking": "BK-1"}, "changed": ["stage"]}),
    )
    assert case_of(log, THE_CLINIC).states == (
        {"stage": "greeting"},
        {"stage": "booked", "booking": "BK-1"},
    )


def test_what_memory_recalled_and_the_knowledge_text_are_evidence() -> None:
    recalled: JsonObject = {
        "ops": [{"op": "recall", "facts": [{"text": "Prefiere la mañana"}], "took_ms": 2.0}]
    }
    config = AgentConfig(slug=THE_CLINIC.slug, knowledge="Revisión: 45 €.")
    case = case_of(a_log(caller("hola"), ("memory.ops", recalled)), config)
    assert case.evidence == ("Revisión: 45 €.", "Prefiere la mañana")


def test_the_callers_own_rule_comes_off_call_started_and_a_persons_call_has_none() -> None:
    started: JsonObject = {
        "channel": "web",
        "direction": "inbound",
        "from": "web_1",
        "to": "clinica-norte",
        "caller": None,
        "started_at": 1.0,
    }
    ruled: JsonObject = {
        **started,
        "persona": "apurado",
        "accepts_when": "a price",
        "declines_when": "",
    }
    assert case_of(a_log(("call.started", started)), None).persona_rule is None
    assert case_of(a_log(("call.started", ruled)), None).persona_rule == ("a price", "")


A_PERSONS_CALL: JsonObject = {
    "channel": "web",
    "direction": "inbound",
    "from": "web_1",
    "to": "clinica-norte",
    "caller": None,
    "started_at": 1.0,
}


def test_a_call_a_named_persona_played_is_a_simulation_and_a_persons_call_is_not() -> None:
    played: JsonObject = {**A_PERSONS_CALL, "persona": "apurado"}
    assert case_of(a_log(("call.started", played)), None).simulated
    assert not case_of(a_log(("call.started", A_PERSONS_CALL)), None).simulated
    assert not case_of(a_log(caller("hola")), None).simulated


def test_a_spoken_caller_sent_whole_without_a_name_is_a_simulation_all_the_same() -> None:
    spoken: JsonObject = {**A_PERSONS_CALL, "from": "simulated_caller"}
    assert case_of(a_log(("call.started", spoken)), None).simulated


def test_an_entry_of_a_shape_it_cannot_read_is_left_out_and_the_rest_is_read() -> None:
    log = a_log(caller("hola"), ("turn.agent", {"words": "old shape"}), agent("buenas", "sp_2"))
    assert [turn.text for turn in case_of(log, None).turns] == ["hola", "buenas"]


def test_every_verdict_livekit_can_return_is_said_in_a_word_of_ours() -> None:
    assert set(VERDICT_WORDS) == set(get_args(Verdict))
    assert verdict_word("fail") == "broken"


def test_skipped_na_and_classified_are_ours_alone_and_livekit_never_writes_them() -> None:
    ours = {"skipped", "na", "classified"}
    assert not ours & set(VERDICT_WORDS.values())
    assert set(VERDICT_WORDS.values()) | ours == set(get_args(ScoreVerdict.__value__))


def test_a_word_is_read_without_its_case_or_the_punctuation_around_it() -> None:
    assert words_of("¿Tú, USTED?") == {"tú", "usted"}


def test_a_declined_booking_is_a_line_of_the_gate_too() -> None:
    log = a_log(confirmation("confirm.declined", call_id="c9"))
    assert [line.kind for line in case_of(log, THE_CLINIC).gate] == ["confirm.declined"]


def test_a_failed_tool_says_so_to_the_judge() -> None:
    log = a_log(
        caller("hola"),
        tool(BOOK.name, {}, call_id="c1"),
        ("tool.result", {"call_id": "c1", "name": BOOK.name, "error": "no slot"}),
        agent("No pude."),
    )
    [called] = case_of(log, THE_CLINIC).turns[1].calls
    assert (called.failed, called.answer) == (True, "no slot")


def test_a_result_of_nothing_is_the_empty_text() -> None:
    log = a_log(
        caller("hola"),
        tool("find_slots", {}, call_id="c1"),
        result("find_slots", "", call_id="c1"),
        agent("nada"),
    )
    [called] = case_of(log, THE_CLINIC).turns[1].calls
    assert called.answer == ""
