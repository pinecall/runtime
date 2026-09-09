"""The gateway fills a turn's markers and remembers a call, and the log says what it did."""

from __future__ import annotations

from functools import partial

import pytest
from cryptography.fernet import Fernet

from pinecall.filling import Filling, OpenCall
from pinecall.log.store import MemoryStore
from pinecall.log.writers import Logs
from pinecall.orgs.vault import MemoryVault, keys_brought_by
from pinecall.providers.embed.tei import DID_NOT_ANSWER
from pinecall.providers.embedder import EmbedderUnreachable
from pinecall.types import Contact, Docs, KnowledgeFile, markers_in
from tests.api.conftest import A_VAULT_KEY
from tests.filling.fakes import (
    CALL,
    ORG,
    THE_NUMBER,
    OneCall,
    ScriptedKnowledge,
    ScriptedMemory,
    a_chunk,
    a_config,
    a_context,
    a_fact,
    a_served_call,
)

pytestmark = pytest.mark.unit

(MEMORY,) = markers_in('<!-- memory: {"kinds":["preference"],"limit":6} -->')
(RETRIEVED,) = markers_in('<!-- retrieved: {"k":2} -->')
(SCORED,) = markers_in('<!-- retrieved: {"min_score":0.5} -->')
(KNOWLEDGE,) = markers_in("<!-- knowledge: ./knowledge/clinica.md -->")

TEI_IS_DOWN = EmbedderUnreachable(
    DID_NOT_ANSWER.format(url="http://127.0.0.1:8081", why="connection refused")
)


async def test_a_memory_marker_on_a_phone_call_recalls_the_number_and_writes_memory_ops() -> None:
    served = a_served_call(
        memory=ScriptedMemory(answers=[a_fact("f1", "prefiere turnos por la mañana")])
    )
    fills = await served.filling.fill(CALL, "quiero un turno", [MEMORY], "sp_2")
    assert fills == {MEMORY.line: "- prefiere turnos por la mañana"}
    [asked] = served.memory.recalled
    assert asked == {
        "org": ORG,
        "contact": THE_NUMBER,
        "query": "quiero un turno",
        "kinds": ("preference",),
        "k": 6,
    }
    [ops] = await served.written("memory.ops")
    assert ops["speech_id"] == "sp_2"
    [op] = ops["ops"]
    assert (op["op"], op["contact"], op["query"]) == ("recall", THE_NUMBER, "quiero un turno")
    assert [fact["text"] for fact in op["facts"]] == ["prefiere turnos por la mañana"]
    assert op["took_ms"] >= 0


async def test_a_resolved_contact_id_outranks_the_number_it_called_from() -> None:
    served = a_served_call(a_context("phone", Contact(id="P-2231", phone=THE_NUMBER)))
    await served.filling.fill(CALL, "hola", [MEMORY], None)
    assert served.memory.recalled[0]["contact"] == "P-2231"


async def test_a_web_call_with_no_identity_is_filled_with_nothing_and_writes_no_entry() -> None:
    served = a_served_call(a_context("web"), memory=ScriptedMemory(answers=[a_fact("f1", "x")]))
    assert await served.filling.fill(CALL, "hola", [MEMORY], "sp_1") == {MEMORY.line: ""}
    assert served.memory.recalled == []
    assert await served.written("memory.ops") == []


async def test_a_retrieved_marker_searches_the_declared_base_and_its_k_overrides_the_configs() -> (
    None
):
    served = a_served_call(
        config=a_config(),
        knowledge=ScriptedKnowledge(
            answers=[
                a_chunk("c1", "Tarifas › Revisión", "Tarifas › Revisión\n\nLa revisión son 45 €."),
                a_chunk(
                    "c2", "Tarifas › Limpieza", "Tarifas › Limpieza\n\nLa limpieza son 60 €.", 0.6
                ),
                a_chunk("c3", "Horarios", "Horarios\n\nDe nueve a seis.", 0.2),
            ]
        ),
    )
    fills = await served.filling.fill(CALL, "¿cuánto cuesta?", [RETRIEVED], "sp_4")
    assert fills[RETRIEVED.line] == (
        "### tarifas.md › Tarifas › Revisión\nLa revisión son 45 €.\n\n"
        "### tarifas.md › Tarifas › Limpieza\nLa limpieza son 60 €."
    )
    [asked] = served.knowledge.searched
    assert (asked["base"], asked["k"], asked["min_score"]) == ("clinica", 2, None)
    [sources] = await served.written("docs.sources")
    assert (sources["query"], sources["speech_id"]) == ("¿cuánto cuesta?", "sp_4")
    assert [(one["id"], one["heading"], one["excerpt"]) for one in sources["sources"]] == [
        ("c1", "Tarifas › Revisión", "La revisión son 45 €."),
        ("c2", "Tarifas › Limpieza", "La limpieza son 60 €."),
    ]


async def test_the_markers_min_score_overrides_the_configs_and_the_configs_stands_otherwise() -> (
    None
):
    served = a_served_call(config=a_config(docs=Docs(base="clinica", k=3, min_score=0.02)))
    await served.filling.fill(CALL, "hola", [SCORED, RETRIEVED], None)
    by_marker = {asked["k"]: asked["min_score"] for asked in served.knowledge.searched}
    assert by_marker == {3: 0.5, 2: 0.02}


async def test_an_agent_that_declared_no_docs_fills_a_retrieved_marker_with_nothing() -> None:
    served = a_served_call(config=a_config(docs=None))
    assert await served.filling.fill(CALL, "hola", [RETRIEVED], None) == {RETRIEVED.line: ""}
    assert served.knowledge.searched == []
    assert await served.written("docs.sources") == []


async def test_a_down_embedder_fills_nothing_and_the_error_entry_names_tei() -> None:
    served = a_served_call(
        knowledge=ScriptedKnowledge(failing=TEI_IS_DOWN),
        memory=ScriptedMemory(failing=TEI_IS_DOWN),
    )
    fills = await served.filling.fill(CALL, "hola", [MEMORY, RETRIEVED], "sp_1")
    assert fills == {MEMORY.line: "", RETRIEVED.line: ""}
    errors = await served.written("error")
    assert sorted((one["code"], one["recoverable"]) for one in errors) == [
        ("memory_skipped", True),
        ("retrieval_skipped", True),
    ]
    for one in errors:
        assert "TEI at http://127.0.0.1:8081 did not answer: connection refused" in one["message"]
    assert errors[0]["message"].startswith(("memory was not filled", "retrieval was not filled"))


async def test_a_knowledge_marker_is_answered_with_the_configs_text_and_no_entry() -> None:
    file = KnowledgeFile("./knowledge/clinica.md", "Abrimos de nueve a seis.")
    served = a_served_call(config=a_config(knowledge=file))
    assert await served.filling.fill(CALL, "hola", [KNOWLEDGE], None) == {
        KNOWLEDGE.line: "Abrimos de nueve a seis."
    }
    assert await served.store.since(CALL) == []


async def test_a_call_this_gateway_does_not_serve_is_filled_with_nothing() -> None:
    served = a_served_call()
    assert await served.filling.fill("call_elsewhere", "hola", [MEMORY], None) == {}


async def test_a_gateway_with_no_tables_answers_every_fill_with_nothing() -> None:
    """A dev key: no Postgres, no memory, no knowledge — and every turn still goes on."""
    logs = Logs(MemoryStore())
    logs.writing(CALL, "clinica-norte")
    opened = OpenCall(org=ORG, context=a_context(), config=a_config())
    filling = Filling(None, None, logs, OneCall(opened), partial(keys_brought_by, None))
    assert await filling.fill(CALL, "hola", [MEMORY, RETRIEVED], None) == {
        MEMORY.line: "",
        RETRIEVED.line: "",
    }
    assert await filling.remember(CALL) == 0


async def test_remember_keeps_the_user_and_agent_turns_and_hands_memory_the_orgs_llm_and_keys() -> (
    None
):
    vault = MemoryVault(Fernet(A_VAULT_KEY.encode()))
    await vault.put(ORG, "anthropic", "sk-the-clinics-own")
    served = a_served_call(vault=vault)
    await served.heard("hola, soy Ana")
    await served.log.append(
        "tool.call", {"call_id": "tu_1", "name": "find_patient", "arguments": {}}
    )
    await served.said("Hola Ana.")

    assert await served.filling.remember(CALL) == 1
    [asked] = served.memory.remembered
    assert [(turn.role, turn.text) for turn in asked["turns"]] == [
        ("user", "hola, soy Ana"),
        ("agent", "Hola Ana."),
    ]
    assert (asked["org"], asked["contact"], asked["channel"], asked["call"]) == (
        ORG,
        THE_NUMBER,
        "phone",
        CALL,
    )
    assert asked["llm"] == a_config().llm
    assert asked["keys"] == {"anthropic": "sk-the-clinics-own"}
    assert asked["policy"] == a_config().memory
    [ops] = await served.written("memory.ops")
    assert ops["ops"][0]["op"] == "remember"


async def test_remember_writes_nothing_for_an_agent_with_no_policy_or_a_caller_with_no_name() -> (
    None
):
    nothing_declared = a_served_call(config=a_config(memory=None))
    assert await nothing_declared.filling.remember(CALL) == 0
    nobody = a_served_call(a_context("web"))
    assert await nobody.filling.remember(CALL) == 0
    assert nobody.memory.remembered == []
