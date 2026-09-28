"""Tests for what a call's agent could know, and what it stated."""

from pinecall.domain.names import JsonObject
from pinecall.evals._evidence import (
    AN_HOUR,
    Evidence,
    Source,
    carries,
    committed_in,
    evidence_of,
    missing_from,
    stated_in,
    tool_call_text,
)
from tests.evals.conftest import agent_line, caller_line, case_of_turns, logged_call

SEEDED: JsonObject = {"patient": {"name": "Ana García", "cita": "jueves a las diez"}}


def test_a_state_read_twice_is_one_block_of_evidence() -> None:
    case = case_of_turns(caller_line("¿Cuándo?"), agent_line("El jueves."), states=(SEEDED, SEEDED))
    assert len(evidence_of(case).state) == 1


# Shown a bare `[]`, a judge failed a right "no hay huecos": the arguments say what it answers.
def test_a_tool_answer_is_read_with_its_name_and_its_arguments() -> None:
    called = logged_call("freeSlots", {"day": "domingo por la mañana"}, "[]")
    assert tool_call_text(called) == 'freeSlots({"day": "domingo por la mañana"}) → []'


def test_a_person_is_the_name_after_the_title_and_not_the_title() -> None:
    case = case_of_turns(agent_line("Le atiende la doctora Vidal a las 09:30 el jueves."))
    assert [(extractor.name, fact) for extractor, fact in stated_in(case)] == [
        ("hour", "09:30"),
        ("date", "jueves"),
        ("person", "Vidal"),
    ]


def test_a_fact_is_found_whatever_the_spacing_and_the_case() -> None:
    evidence = Evidence(text=(), calls=("slots → 10 : 00",))
    assert carries(evidence, "10:00", Source.CALL)
    assert not carries(evidence, "10:00", Source.TEXT)


def test_an_hour_only_in_the_written_evidence_is_named_as_a_near_miss() -> None:
    evidence = Evidence(text=("Horario: 09:30 a 20:00.",), calls=())
    assert missing_from(evidence, AN_HOUR, "09:30") == (
        "no call evidence carries the hour '09:30', though the text does"
    )


def test_the_phrases_that_commit_are_found_in_both_languages() -> None:
    turns = [
        "Perfecto, un técnico pasará el martes.",
        "I'll call you back tomorrow.",
        "La revisión le costará 45 euros.",
        "¿Me dice su nombre?",
    ]
    assert committed_in(turns) == ("un técnico pasará", "I'll call", "le costará")


def test_what_retrieval_showed_and_the_knowledge_text_are_text_evidence() -> None:
    case = case_of_turns(
        agent_line("Son 45 euros.", retrieved=("Revisión: 45 €.",)), evidence=("Limpieza: 60 €.",)
    )
    assert evidence_of(case).text == ("Limpieza: 60 €.", "Revisión: 45 €.")
