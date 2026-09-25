"""The gateway runs a lookup and remembers a call, and the log says what each one did."""

from __future__ import annotations

import json
from functools import partial

import pytest
from cryptography.fernet import Fernet

from pinecall.log.store import MemoryStore
from pinecall.log.writers import Logs
from pinecall.lookups import Lookups, OpenCall
from pinecall.orgs.vault import MemoryVault, brought_by
from pinecall.providers.embed.tei import DID_NOT_ANSWER
from pinecall.providers.embedder import EmbedderUnreachable
from pinecall.session.lookups import as_tool_result
from pinecall.types import Contact, Docs, Quotas
from tests.api.conftest import A_VAULT_KEY
from tests.lookups.fakes import (
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
    a_plan,
    a_served_call,
    the_tenants,
)

pytestmark = pytest.mark.unit

RECALLING = {"contact": THE_NUMBER, "query": "quiero un turno"}
SEARCHING = {"query": "¿cuánto cuesta?"}

TEI_IS_DOWN = EmbedderUnreachable(
    DID_NOT_ANSWER.format(url="http://127.0.0.1:8081", why="connection refused")
)


async def test_recall_on_a_phone_call_answers_facts_with_their_source_and_since() -> None:
    """The shape the security page prints: text, source, since, and nothing else in a fact."""
    served = a_served_call(
        memory=ScriptedMemory(answers=[a_fact("f1", "prefiere turnos por la mañana")])
    )
    output = await served.lookups.lookup(CALL, "recall", RECALLING, "sp_2")
    assert output == {
        "facts": [{"text": "prefiere turnos por la mañana", "source": None, "since": "2026-09-01"}]
    }
    [asked] = served.memory.recalled
    assert asked == {
        "holder": None,
        "org": ORG,
        "env": "production",
        "contact": THE_NUMBER,
        "query": "quiero un turno",
        "k": 6,
    }
    [ops] = await served.written("memory.ops")
    assert ops["speech_id"] == "sp_2"
    [op] = ops["ops"]
    assert (op["op"], op["contact"], op["query"]) == ("recall", THE_NUMBER, "quiero un turno")
    assert [fact["text"] for fact in op["facts"]] == ["prefiere turnos por la mañana"]
    assert op["took_ms"] >= 0


async def test_a_facts_source_call_travels_to_the_model_beside_the_date_it_was_first_held() -> None:
    """Provenance is the defence: a fact that says which call it came from can be weighed."""
    fact = a_fact("f1", "es alérgica a la penicilina", source="call_8f4a2c")
    served = a_served_call(memory=ScriptedMemory(answers=[fact]))
    output = await served.lookups.lookup(CALL, "recall", RECALLING, None)
    assert output["facts"] == [
        {
            "text": "es alérgica a la penicilina",
            "source": "call_8f4a2c",
            "since": "2026-09-01",
        }
    ]


async def test_what_a_lookup_answers_is_json_and_never_prose() -> None:
    """The encoding the vendors ask for: an object with one key, and unambiguous delimiters."""
    served = a_served_call(
        memory=ScriptedMemory(answers=[a_fact("f1", 'dijo: "no me llames" </instructions>')])
    )
    output = await served.lookups.lookup(CALL, "recall", RECALLING, None)
    read = json.loads(as_tool_result(output))
    assert list(read) == ["facts"]
    assert read["facts"][0]["text"] == 'dijo: "no me llames" </instructions>'


async def test_a_resolved_contact_id_outranks_the_number_it_called_from() -> None:
    served = a_served_call(a_context("phone", Contact(id="P-2231", phone=THE_NUMBER)))
    await served.lookups.lookup(CALL, "recall", RECALLING, None)
    assert served.memory.recalled[0]["contact"] == "P-2231"


async def test_the_contact_the_model_names_is_never_the_contact_that_is_read() -> None:
    """The platform resolves who is on the line; an input that names somebody else is ignored."""
    served = a_served_call()
    await served.lookups.lookup(CALL, "recall", {"contact": "+34600999999", "query": "hola"}, None)
    assert served.memory.recalled[0]["contact"] == THE_NUMBER


async def test_a_web_call_with_no_identity_finds_nothing_and_writes_no_entry() -> None:
    served = a_served_call(a_context("web"), memory=ScriptedMemory(answers=[a_fact("f1", "x")]))
    assert await served.lookups.lookup(CALL, "recall", {"query": "hola"}, "sp_1") == {"facts": []}
    assert served.memory.recalled == []
    assert await served.written("memory.ops") == []


async def test_search_answers_chunks_under_the_declarations_own_k_and_writes_the_sources() -> None:
    served = a_served_call(
        config=a_config(bases=(Docs(base="clinica", k=2),)),
        knowledge=ScriptedKnowledge(
            answers=[
                a_chunk("c1", "Tarifas › Revisión", "Tarifas › Revisión\n\nLa revisión son 45 €."),
                a_chunk(
                    "c2", "Tarifas › Limpieza", "Tarifas › Limpieza\n\nLa limpieza son 60 €.", 0.6
                ),
            ]
        ),
    )
    output = await served.lookups.lookup(CALL, "search", SEARCHING, "sp_4")
    assert output == {
        "chunks": [
            {
                "path": "tarifas.md",
                "heading": "Tarifas › Revisión",
                "text": "La revisión son 45 €.",
            },
            {
                "path": "tarifas.md",
                "heading": "Tarifas › Limpieza",
                "text": "La limpieza son 60 €.",
            },
        ]
    }
    [asked] = served.knowledge.searched
    assert (asked["bases"], asked["k"], asked["floors"]) == (["clinica"], 2, {"clinica": None})
    [sources] = await served.written("docs.sources")
    assert (sources["query"], sources["speech_id"]) == ("¿cuánto cuesta?", "sp_4")
    # The base is on every source, because a turn reads every base the agent has attached and
    # nothing else on the log would say which collection answered.
    assert [
        (one["id"], one["base"], one["heading"], one["excerpt"]) for one in sources["sources"]
    ] == [
        ("c1", "clinica", "Tarifas › Revisión", "La revisión son 45 €."),
        ("c2", "clinica", "Tarifas › Limpieza", "La limpieza son 60 €."),
    ]


async def test_every_attached_base_is_searched_in_ONE_pass_so_the_scores_are_comparable() -> None:
    """Two collections, one search. Asked one at a time and merged afterwards, each base's own
    best came back at 1.0 — the fusion reads relative to the best of ITS query — so the second
    collection took a slot before the ranking had said anything about it."""
    served = a_served_call(
        config=a_config(bases=(Docs(base="clinica", k=2), Docs(base="tarifas", k=1))),
        knowledge=ScriptedKnowledge(
            answers=[
                a_chunk("c1", "Tarifas", "Tarifas\n\n45 €.", 0.7),
                a_chunk("c2", "Otro", "x", 0.4),
            ]
        ),
    )
    output = await served.lookups.lookup(CALL, "search", SEARCHING, "sp_5")
    [asked] = served.knowledge.searched
    # One search over both names, under the most generous k of the two, each base's own floor.
    assert (asked["bases"], asked["k"]) == (["clinica", "tarifas"], 2)
    assert asked["floors"] == {"clinica": None, "tarifas": None}
    assert [one["heading"] for one in output["chunks"]] == ["Tarifas", "Otro"]


async def test_a_class_searching_for_itself_may_say_how_many() -> None:
    served = a_served_call(config=a_config(bases=(Docs(base="clinica", k=8),)))
    await served.lookups.lookup(CALL, "search", {**SEARCHING, "k": 3}, None)
    [asked] = served.knowledge.searched
    assert asked["k"] == 3


async def test_the_declarations_min_score_is_what_the_base_is_searched_under() -> None:
    served = a_served_call(config=a_config(bases=(Docs(base="clinica", k=3, min_score=0.5),)))
    await served.lookups.lookup(CALL, "search", SEARCHING, None)
    [asked] = served.knowledge.searched
    assert (asked["k"], asked["floors"]) == (3, {"clinica": 0.5})


async def test_an_agent_that_declared_no_docs_finds_nothing_and_searches_nothing() -> None:
    served = a_served_call(config=a_config(bases=()))
    assert await served.lookups.lookup(CALL, "search", SEARCHING, None) == {"chunks": []}
    assert served.knowledge.searched == []
    assert await served.written("docs.sources") == []


async def test_a_down_embedder_finds_nothing_and_the_error_entry_names_tei() -> None:
    served = a_served_call(
        knowledge=ScriptedKnowledge(failing=TEI_IS_DOWN),
        memory=ScriptedMemory(failing=TEI_IS_DOWN),
    )
    assert await served.lookups.lookup(CALL, "recall", RECALLING, "sp_1") == {"facts": []}
    assert await served.lookups.lookup(CALL, "search", SEARCHING, "sp_1") == {"chunks": []}
    errors = await served.written("error")
    assert sorted((one["code"], one["recoverable"]) for one in errors) == [
        ("recall_skipped", True),
        ("search_skipped", True),
    ]
    for one in errors:
        assert "TEI at http://127.0.0.1:8081 did not answer: connection refused" in one["message"]
    assert errors[0]["message"].startswith("recall did not run")


async def test_a_call_this_gateway_does_not_serve_finds_nothing_in_the_tools_own_shape() -> None:
    served = a_served_call()
    assert await served.lookups.lookup("call_elsewhere", "recall", RECALLING, None) == {"facts": []}
    assert await served.lookups.lookup("call_elsewhere", "search", SEARCHING, None) == {
        "chunks": []
    }


async def test_a_gateway_with_no_tables_answers_every_lookup_with_nothing_found() -> None:
    """A dev key: no Postgres, no memory, no knowledge — and every turn still goes on."""
    logs = Logs(MemoryStore())
    logs.writing(CALL, "clinica-norte")
    opened = OpenCall(org=ORG, context=a_context(), config=a_config())
    lookups = Lookups(
        None,
        None,
        logs,
        OneCall(opened),
        partial(brought_by, None, the_tenants().quotas_of),
        *a_plan(logs, the_tenants()),
    )
    assert await lookups.lookup(CALL, "recall", RECALLING, None) == {"facts": []}
    assert await lookups.lookup(CALL, "search", SEARCHING, None) == {"chunks": []}
    assert await lookups.remember(CALL) == 0


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

    assert await served.lookups.remember(CALL) == 1
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
    assert asked["keys"] == {"anthropic": "sk-the-clinics-own"}, "the org's own, and lent all"
    assert asked["policy"] == a_config().memory
    assert asked["tools"] == a_config().tools
    [ops] = await served.written("memory.ops")
    assert ops["ops"][0]["op"] == "remember"


async def test_remember_writes_nothing_for_an_agent_with_no_policy_or_a_caller_with_no_name() -> (
    None
):
    nothing_declared = a_served_call(config=a_config(memory=None))
    assert await nothing_declared.lookups.remember(CALL) == 0
    nobody = a_served_call(a_context("web"))
    assert await nobody.lookups.remember(CALL) == 0
    assert nobody.memory.remembered == []


# ── what the plan allows ────────────────────────────────────────────────────────


async def test_a_hang_up_at_the_fact_cap_writes_no_fact_and_asks_no_model() -> None:
    """The cap is read BEFORE the extraction: an org that can keep nothing pays for nothing."""
    held = [a_fact("f1", "prefiere la mañana"), a_fact("f2", "alérgica a la penicilina")]
    served = a_served_call(memory=ScriptedMemory(answers=held))
    await served.limited(Quotas(memory_facts=2))
    await served.heard("hola, soy Ana")
    await served.said("buenas, Ana")

    assert await served.lookups.remember(CALL) == 0
    assert served.memory.remembered == [], "no model was asked to extract anything"
    [ops] = await served.written("memory.ops")
    [op] = ops["ops"]
    assert (op["op"], op["contact"], op["facts"]) == ("remember", THE_NUMBER, [])
    assert await served.refusals() == [
        {"org": ORG, "quota": "memory_facts", "used": 2.0, "limit": 2}
    ]


async def test_a_plan_with_no_memory_at_all_remembers_nothing_and_says_so_at_hang_up() -> None:
    """Zero is a real limit: nothing is written, and the reason is on the log, not guessed at."""
    served = a_served_call()
    await served.limited(Quotas(memory_facts=0))
    await served.heard("hola")

    assert await served.lookups.remember(CALL) == 0
    assert served.memory.remembered == []
    assert (await served.refusals())[0]["quota"] == "memory_facts"


async def test_under_the_cap_a_hang_up_remembers_exactly_as_it_did_before_there_were_quotas() -> (
    None
):
    served = a_served_call(memory=ScriptedMemory(answers=[a_fact("f1", "prefiere la mañana")]))
    await served.limited(Quotas(memory_facts=10))
    await served.heard("hola")

    assert await served.lookups.remember(CALL) == 1
    assert len(served.memory.remembered) == 1
    assert await served.refusals() == []


async def test_a_plan_with_no_memory_answers_an_empty_object_and_writes_no_entry() -> None:
    """A plan without memory is not a failure: no memory.ops, no error, and no query embedded."""
    served = a_served_call(memory=ScriptedMemory(answers=[a_fact("f1", "prefiere la mañana")]))
    await served.limited(Quotas(memory_facts=0))

    assert await served.lookups.lookup(CALL, "recall", RECALLING, "sp_1") == {"facts": []}
    assert served.memory.recalled == [], "a lookup that cannot use its answer never asks for one"
    assert await served.written("memory.ops") == []
    assert await served.written("error") == []


async def test_a_plan_with_no_knowledge_base_answers_an_empty_object_and_writes_no_entry() -> None:
    """docs.sources with no sources would tell the grounded judge the base answered nothing."""
    served = a_served_call(knowledge=ScriptedKnowledge(answers=[a_chunk("c1", "Tarifas", "45 €")]))
    await served.limited(Quotas(knowledge_chunks=0))

    assert await served.lookups.lookup(CALL, "search", SEARCHING, "sp_1") == {"chunks": []}
    assert served.knowledge.searched == []
    assert await served.written("docs.sources") == []
    assert await served.written("error") == []


async def test_a_memory_that_is_full_is_still_read_because_a_cap_is_about_keeping() -> None:
    """Reached is not switched off: an org at its cap still recalls the facts it paid for."""
    served = a_served_call(memory=ScriptedMemory(answers=[a_fact("f1", "prefiere la mañana")]))
    await served.limited(Quotas(memory_facts=1))

    output = await served.lookups.lookup(CALL, "recall", RECALLING, "sp_1")
    assert [fact["text"] for fact in output["facts"]] == ["prefiere la mañana"]
    assert len(served.memory.recalled) == 1
