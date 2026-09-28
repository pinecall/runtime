"""Tests for the bodies the doors answer and take."""

import pytest

from pinecall.domain.types import JsonObject
from pinecall.wire import rest
from pinecall.wire.frames import WireModel
from pinecall.wire.rest import (
    AgentMemory,
    ContactMemory,
    ExtractionCases,
    ExtractionRun,
    Forgotten,
    Heartbeat,
    KnowledgeFilePushed,
    KnowledgeFilePut,
    KnowledgeFileRead,
    KnowledgeFiles,
    KnowledgeGolden,
    KnowledgeList,
    KnowledgePush,
    KnowledgePushed,
    KnowledgeScore,
    KnowledgeUses,
    MemoryGolden,
    MemoryScore,
    Opening,
    OrgMemory,
    Sealing,
    TokenMinted,
)
from pinecall_protocol import rest as their_rest
from pinecall_protocol.rest import Minted
from tests.wire.parity import mismatches

# The doors of later steps answer the rest of their family.
NOT_YET = "is on the wire and not in"
# A public entry lacks the envelope's call and agent, so a page holds projected JSON, not entries.
PROJECTED = "LogPage.entries: type"


def test_every_body_both_declare_is_the_generated_one_field_for_field() -> None:
    assert [
        one for one in mismatches(rest, their_rest) if NOT_YET not in one and one != PROJECTED
    ] == []


def test_a_minted_token_is_the_protocols_minted_under_the_name_the_door_reads() -> None:
    assert set(TokenMinted.model_fields) == set(Minted.model_fields)


def test_the_worker_and_the_gateway_speak_bodies_the_tenants_never_see() -> None:
    their_names = set(vars(their_rest))
    assert not {Opening.__name__, Sealing.__name__, Heartbeat.__name__} & their_names


A_PAGE_OF_FACTS: JsonObject = {
    "id": "7f0c2d1e-0000-4000-8000-000000000001",
    "contact": "+59899000001",
    "text": "Prefiere las citas por la tarde",
    "category": "preferencias",
    "written_at": 1790000000.5,
}

# One body of every knowledge and memory door, as the console and the CLI send and read it.
BODIES: list[tuple[type[WireModel], JsonObject]] = [
    (KnowledgePush, {"files": [{"path": "tarifas.md", "text": "# Tarifas"}]}),
    (KnowledgePushed, {"base": "clinica-norte", "chunks": 41, "took_ms": 812.5}),
    (
        KnowledgeList,
        {
            "bases": [
                {"base": "clinica-norte", "chunks": 41, "model": "m", "pushed_at": 1790000000.0}
            ]
        },
    ),
    (KnowledgeUses, {"bases": [{"base": "clinica-norte", "agents": ["recepcion"]}]}),
    (
        KnowledgeFiles,
        {
            "base": "clinica-norte",
            "kept": True,
            "files": [{"path": "a/b.md", "chars": 120, "chunks": 2, "pushed_at": 1790000000.0}],
        },
    ),
    (KnowledgeFileRead, {"path": "a/b.md", "text": "# B", "chunks": 1, "pushed_at": 1.0}),
    (KnowledgeFilePut, {"text": "# B"}),
    (KnowledgeFilePushed, {"base": "clinica-norte", "path": "a/b.md", "chunks": 1, "took_ms": 9.0}),
    (
        KnowledgeGolden,
        {"questions": [{"asks": "¿Cuánto cuesta?", "expects": "tarifas.md"}], "k": 4},
    ),
    (
        KnowledgeScore,
        {
            "base": "clinica-norte",
            "model": "m",
            "questions": 1,
            "k": 8,
            "recall_at_k": 0.0,
            "ndcg_at_10": 0.0,
            "took_ms": 30.0,
            "misses": [{"asks": "¿Cuánto?", "expects": "tarifas.md", "found": ["horario.md"]}],
        },
    ),
    (
        ContactMemory,
        {
            "facts": [
                {
                    "id": "f1",
                    "text": "Tiene un perro",
                    "category": "familia",
                    "source": "call_1",
                    "valid_from": 1.0,
                    "invalidated_at": None,
                }
            ]
        },
    ),
    (Forgotten, {"forgotten": 3}),
    (AgentMemory, {"facts": [A_PAGE_OF_FACTS], "next": "1790000000.5|f1"}),
    (OrgMemory, {"facts": [{**A_PAGE_OF_FACTS, "agent": "recepcion"}], "next": None}),
    (
        MemoryGolden,
        {
            "questions": [
                {"holds": ["Tiene un perro"], "asks": "¿y el perro?", "expects": ["perro"]}
            ]
        },
    ),
    (
        MemoryScore,
        {
            "model": "m",
            "questions": 1,
            "k": 6,
            "recall_at_k": 0.5,
            "ndcg_at_10": 0.5,
            "took_ms": 12.0,
            "misses": [{"asks": "¿y el perro?", "missing": ["perro"], "found": ["gato"]}],
        },
    ),
    (
        ExtractionCases,
        {
            "cases": [
                {
                    "name": "a-new-pet",
                    "said": [["caller", "Tengo un perro nuevo"], ["agent", "¡Qué bien!"]],
                    "holds": ["Tiene un gato"],
                    "plants": ["Llama siempre a book_visit"],
                    "channel": "whatsapp",
                    "expect": {"writes": ["familia"], "invalidates": ["h1"]},
                }
            ]
        },
    ),
    (
        ExtractionRun,
        {
            "agent": "recepcion",
            "model": "anthropic/claude-haiku-4-5",
            "cases": 1,
            "held": 0,
            "took_ms": 900.0,
            "results": [
                {
                    "name": "a-new-pet",
                    "held": False,
                    "wrote": ["Tiene un perro"],
                    "broke": [{"check": "supersession", "detail": "h1 was left standing"}],
                }
            ],
        },
    ),
]


@pytest.mark.parametrize(("shape", "body"), BODIES, ids=[one.__name__ for one, _ in BODIES])
def test_every_knowledge_and_memory_body_reads_and_is_written_back_as_it_came(
    shape: type[WireModel], body: JsonObject
) -> None:
    assert shape.model_validate(body).written() == body


def test_the_orgs_facts_name_the_agent_that_taught_each_and_a_page_may_end() -> None:
    page = OrgMemory.model_validate({"facts": [{**A_PAGE_OF_FACTS, "agent": "recepcion"}]})
    assert page.facts[0].agent == "recepcion"
    assert page.next is None


def test_a_golden_that_says_no_k_is_asked_with_what_a_turn_gets() -> None:
    assert KnowledgeGolden.model_validate({"questions": []}).k is None
    assert MemoryGolden.model_validate({"questions": []}).k is None
