"""Tests for what a call's agent could know, as a judge that reads the evidence is shown it."""

from pinecall.domain.names import JsonObject
from pinecall.evals._evidence import Evidence, as_text, evidence_of, tool_call_text
from pinecall.evals.case import Called
from tests.evals.conftest import agent_line, caller_line, case_of_turns, logged_call

SEEDED: JsonObject = {"patient": {"name": "Ana García", "cita": "jueves a las diez"}}


def test_a_state_read_twice_is_one_block_of_evidence() -> None:
    case = case_of_turns(caller_line("¿Cuándo?"), agent_line("El jueves."), states=(SEEDED, SEEDED))
    assert len(evidence_of(case).state) == 1


# Shown a bare `[]`, a judge failed a right "no hay huecos": the arguments say what it answers.
def test_a_tool_answer_is_read_with_its_name_and_its_arguments() -> None:
    called = logged_call("freeSlots", {"day": "domingo por la mañana"}, "[]")
    assert tool_call_text(called) == 'freeSlots({"day": "domingo por la mañana"}) → []'


def test_what_retrieval_showed_and_the_knowledge_text_are_text_evidence() -> None:
    case = case_of_turns(
        agent_line("Son 45 euros.", retrieved=("Revisión: 45 €.",)), evidence=("Limpieza: 60 €.",)
    )
    assert evidence_of(case).text == ("Limpieza: 60 €.", "Revisión: 45 €.")


def test_a_tool_call_that_never_answered_is_no_evidence() -> None:
    unanswered = Called(call_id="c1", name="book", arguments={"day": "viernes"}, answer=None)
    answered = logged_call("find_slots", {"day": "viernes"}, "10:00")
    case = case_of_turns(agent_line("Le busco hueco.", calls=(unanswered, answered)))
    assert evidence_of(case).calls == ('find_slots({"day": "viernes"}) → 10:00',)


def test_the_evidence_reads_text_then_tool_calls_then_states_one_block_apart() -> None:
    evidence = Evidence(text=("Revisión: 45 €.",), calls=("slots() → 10:00",), state=('{"a": 1}',))
    assert as_text(evidence) == 'Revisión: 45 €.\n\nslots() → 10:00\n\n{"a": 1}'
