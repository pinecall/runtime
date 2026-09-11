"""The bridge over the repo's golden call: every turn, every contract, every metric, unrenamed."""

from __future__ import annotations

import pytest

from pinecall.evals import Case, Said, a_case
from tests.evals.logs import THE_GOLDENS_TOOLS, the_golden_call

pytestmark = pytest.mark.unit

# The turn of the golden this file asks about most: the one that answered after retrieval and a
# tool, so it carries a source, a call, a metrics block and every typed block of its own speech.
QUOTED_THE_TARIFF = "El jueves tengo a las nueve y media o a las once."

THE_KNOWLEDGE = "Revisión: 45 €. Limpieza: 60 €."


def a_golden_case() -> Case:
    """The golden call as a judge reads it, with the agent's own two tools declared."""
    return a_case(the_golden_call(), tools=THE_GOLDENS_TOOLS, knowledge=[THE_KNOWLEDGE])


def the_turn_that(begins: str) -> Said:
    """The one turn of the golden whose text starts a given way."""
    return next(turn for turn in a_golden_case().turns if turn.text.startswith(begins))


def test_the_turns_come_out_in_the_order_the_log_wrote_them() -> None:
    turns = a_golden_case().turns

    assert [turn.role for turn in turns[:4]] == ["assistant", "user", "assistant", "user"]
    assert sum(1 for turn in turns if turn.role == "assistant") == 7


def test_a_tool_call_carries_its_arguments_its_contract_and_what_the_model_read_back() -> None:
    called = [call for turn in a_golden_case().turns for call in turn.calls]

    booking = next(call for call in called if call.name == "book_slot")
    assert booking.arguments == {
        "doctor": "vidal",
        "at": "2026-08-13T09:30",
        "patient": "P-2231",
    }
    assert "BK-5521" in (booking.answer or "")
    assert not booking.failed
    assert booking.description == THE_GOLDENS_TOOLS["book_slot"].description


def test_a_turn_keeps_livekits_own_metric_names_and_the_typed_blocks_beside_them() -> None:
    """Nothing is summarised, renamed or converted: a latency budget reads what the log wrote."""
    spoke = the_turn_that(QUOTED_THE_TARIFF)

    assert spoke.metrics["e2e_latency"] == 1.72
    assert spoke.metrics["llm_node_ttft"] == 0.51
    # livekit's own discriminator, kept: the entry type `metrics.llm` and the payload's own
    # `llm_metrics` are the same word, and only one of them is livekit's.
    assert {block["type"] for block in spoke.blocks} == {
        "eou_metrics",
        "llm_metrics",
        "tts_metrics",
    }


def test_what_retrieval_put_in_front_of_the_model_travels_with_the_turn_it_was_for() -> None:
    spoke = the_turn_that(QUOTED_THE_TARIFF)

    assert THE_KNOWLEDGE in "\n".join(spoke.retrieved)


def test_the_gate_trace_is_in_seq_order_and_says_what_each_tool_does() -> None:
    gate = a_golden_case().gate

    assert [line.seq for line in gate] == sorted(line.seq for line in gate)
    assert [(line.kind, line.tool) for line in gate] == [
        ("tool.call", "find_slots"),
        ("tool.call", "book_slot"),
        ("confirm.request", "book_slot"),
        ("confirm.granted", "book_slot"),
    ]
    assert {line.tool: line.side_effect for line in gate if line.kind == "tool.call"} == {
        "find_slots": "read",
        "book_slot": "irreversible",
    }


def test_the_declaration_travels_whole_so_a_judge_can_read_what_was_promised() -> None:
    contract = a_golden_case().contracts["book_slot"]

    assert contract["side_effect"] == "irreversible"
    assert contract["parameters"]["type"] == "object"
    assert "¿Confirmo?" in contract["confirm"]


def test_the_calls_own_summary_lands_verbatim_so_nothing_recomputes_a_cost() -> None:
    summary = a_golden_case().summary

    assert summary is not None
    assert summary["turns"] == 11
    assert summary["cost"]["eur"] > 0
    assert summary["usage"]


def test_every_fact_that_arrived_lands_on_the_case_with_the_seq_that_joins_it_to_a_turn() -> None:
    """A golden asks whether the agent took an outside fact up; the fact and its place both go."""
    case = a_golden_case()
    arrived = case.events

    assert [(fact.name, fact.source) for fact in arrived] == [("slot.released", "app")]
    assert arrived[0].data["at"] == "2026-08-13T10:15"
    said_after = [
        turn for turn in case.turns if turn.role == "assistant" and turn.seq > arrived[0].seq
    ]
    assert said_after, "the golden call's agent never spoke after the fact that arrived"


def test_the_case_hands_livekit_the_conversation_with_every_call_and_its_answer() -> None:
    """A judge is given a ChatContext and nothing else, so the turns have to reach it whole."""
    items = a_golden_case().chat_ctx.items

    assert [item.name for item in items if item.type == "function_call"] == [
        "find_slots",
        "book_slot",
    ]
    assert [item.name for item in items if item.type == "function_call_output"] == [
        "find_slots",
        "book_slot",
    ]
    # Twelve turn entries: seven of the agent's and five of the caller's. `call.summary` counts
    # eleven, which is the session's own number and not this one — neither is recomputed here.
    assert sum(1 for item in items if item.type == "message") == 12


def test_every_state_the_call_was_in_comes_out_whole_and_in_order() -> None:
    """`state.changed` carries the whole state, so the bridge files it and reconstructs nothing."""
    states = a_golden_case().states

    assert [state["stage"] for state in states] == ["greeting", "choosing", "choosing", "booked"]
    # The last one carries the booking the last tool wrote, and the patient it never touched.
    assert states[-1]["booking"] == "BK-5521"
    assert states[-1]["patient"] == {"id": "P-2231", "name": "Marta Ruiz"}
