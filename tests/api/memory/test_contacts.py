"""A contact's memory as the tenant reads it, and as the contact has it erased."""

from __future__ import annotations

import httpx
import pytest

from pinecall.api.deps import NO_MEMORY
from pinecall.memory.protocol import DEFAULT_FACTS_PER_TURN
from pinecall.orgs.table import MemoryOrgs
from pinecall.types import Quotas
from tests.api.conftest import A_RECORD
from tests.lookups.fakes import LEARNED, ScriptedMemory, a_fact
from tests.vectors import HASH_MODEL

pytestmark = pytest.mark.unit

A_CONTACT = "/v1/contacts/+34600000001/memory"


@pytest.fixture
def memory() -> ScriptedMemory:
    """Two facts memory holds about the one contact."""
    return ScriptedMemory(
        answers=[a_fact("f1", "prefiere turnos por la mañana"), a_fact("f2", "vive en Montevideo")]
    )


async def test_the_history_then_forgetting_it(
    tenant_http: httpx.AsyncClient, memory: ScriptedMemory
) -> None:
    read = await tenant_http.get(A_CONTACT)
    assert read.status_code == 200
    facts = read.json()["facts"]
    assert [fact["text"] for fact in facts] == [
        "prefiere turnos por la mañana",
        "vive en Montevideo",
    ]
    assert facts[0]["valid_from"] == LEARNED.timestamp()
    assert facts[0]["invalidated_at"] is None
    assert facts[0]["category"] == "preference"

    forgotten = await tenant_http.delete(A_CONTACT)
    assert forgotten.status_code == 200
    assert forgotten.json() == {"forgotten": 2}
    assert memory.answers == []
    assert (await tenant_http.get(A_CONTACT)).json() == {"facts": []}


async def test_forgetting_a_stranger_answers_zero_and_never_404(
    tenant_http: httpx.AsyncClient,
) -> None:
    forgotten = await tenant_http.delete("/v1/contacts/nobody/memory")
    assert (forgotten.status_code, forgotten.json()) == (200, {"forgotten": 2})


# Erasing is a right and not a feature: whatever an org's plan says, the contact who asks what
# is known about them is told, and the contact who asks to be forgotten is forgotten.
async def test_reading_and_forgetting_work_on_a_plan_that_keeps_no_memory_at_all(
    tenant_http: httpx.AsyncClient, memory: ScriptedMemory, orgs: MemoryOrgs
) -> None:
    await orgs.set_quotas(A_RECORD.org, Quotas(memory_facts=0, knowledge_chunks=0))
    read = await tenant_http.get(A_CONTACT)
    assert (read.status_code, len(read.json()["facts"])) == (200, 2)
    forgotten = await tenant_http.delete(A_CONTACT)
    assert (forgotten.status_code, forgotten.json()) == (200, {"forgotten": 2})
    assert memory.answers == []


class TestOnADevKey:
    """No Postgres: a contact's memory is nowhere, and both doors say so in one sentence."""

    @pytest.fixture
    def memory(self) -> None:
        return None

    async def test_both_doors_answer_503_and_the_sentence(
        self, tenant_http: httpx.AsyncClient
    ) -> None:
        for answer in (await tenant_http.get(A_CONTACT), await tenant_http.delete(A_CONTACT)):
            assert answer.status_code == 503
            assert answer.json()["detail"] == NO_MEMORY
        assert "no database answered" in NO_MEMORY
        assert "migrate up" in NO_MEMORY


# ── the golden ──────────────────────────────────────────────────────────────────

EVAL = "/v1/contacts/memory/eval"

A_QUESTION = {
    "holds": ["Prefiere mañanas", "Alérgica a la penicilina"],
    "asks": "¿le va bien el martes?",
    "expects": ["Prefiere mañanas"],
}


# A golden is the only thing that can say memory returned the WRONG facts: the judge that runs on
# every call weighs what the agent said against the facts it was handed, and never sees the better
# one that was missed. docs/retrieval/spec.md.
class TestAGolden:
    """The questions bring their own facts, so this memory starts empty and ends empty."""

    @pytest.fixture
    def memory(self) -> ScriptedMemory:
        """A memory holding nothing about anybody: what a golden needs and all it needs."""
        return ScriptedMemory()

    async def test_it_writes_its_own_facts_asks_them_and_leaves_no_contact_behind(
        self, tenant_http: httpx.AsyncClient, memory: ScriptedMemory
    ) -> None:
        said = (await tenant_http.post(EVAL, json={"questions": [A_QUESTION], "k": 4})).json()
        assert (said["questions"], said["k"]) == (1, 4)
        assert (said["recall_at_k"], said["ndcg_at_10"]) == (1.0, 1.0)
        assert said["misses"] == []
        assert said["model"] == HASH_MODEL
        assert said["took_ms"] >= 0
        # The facts were the question's own and the scratch contact is gone: a golden reads no
        # contact of this org and writes none of them either.
        assert memory.answers == []
        (asked,) = memory.recalled
        assert (asked["query"], asked["k"]) == ("¿le va bien el martes?", 4)
        assert asked["contact"].startswith("golden-")

    async def test_a_question_it_misses_names_what_was_missing_and_what_came_back(
        self, tenant_http: httpx.AsyncClient
    ) -> None:
        missed = {**A_QUESTION, "expects": ["Vive en Pocitos"]}
        said = (await tenant_http.post(EVAL, json={"questions": [missed]})).json()
        assert (said["recall_at_k"], said["ndcg_at_10"]) == (0.0, 0.0)
        (miss,) = said["misses"]
        assert miss["asks"] == "¿le va bien el martes?"
        assert miss["missing"] == ["Vive en Pocitos"]
        assert miss["found"] == ["Prefiere mañanas", "Alérgica a la penicilina"]

    async def test_each_question_is_asked_of_its_own_facts_and_nobody_elses(
        self, tenant_http: httpx.AsyncClient
    ) -> None:
        """The contact is emptied between questions, or the second one answers from the first."""
        other = {
            "holds": ["Vive en Pocitos"],
            "asks": "¿dónde vive?",
            "expects": ["Vive en Pocitos"],
        }
        said = (await tenant_http.post(EVAL, json={"questions": [A_QUESTION, other]})).json()
        assert (said["questions"], said["recall_at_k"]) == (2, 1.0)
        assert said["misses"] == []

    async def test_a_golden_asked_with_no_k_is_asked_with_the_facts_a_turn_gets(
        self, tenant_http: httpx.AsyncClient, memory: ScriptedMemory
    ) -> None:
        said = (await tenant_http.post(EVAL, json={"questions": [A_QUESTION]})).json()
        assert said["k"] == DEFAULT_FACTS_PER_TURN
        assert memory.recalled[0]["k"] == DEFAULT_FACTS_PER_TURN

    async def test_the_scratch_contact_is_forgotten_even_when_a_question_fails(
        self, tenant_http: httpx.AsyncClient, memory: ScriptedMemory
    ) -> None:
        """Facts of a half-run golden left behind would count against the org's own quota."""
        memory.failing = RuntimeError("the embedder did not answer")
        with pytest.raises(RuntimeError):
            await tenant_http.post(EVAL, json={"questions": [A_QUESTION]})
        assert memory.answers == []


class TestAGoldenOnADevKey:
    """No Postgres, no table to write a scratch contact into: the sentence every door answers."""

    @pytest.fixture
    def memory(self) -> None:
        return None

    async def test_it_answers_503_and_the_same_sentence(
        self, tenant_http: httpx.AsyncClient
    ) -> None:
        refused = await tenant_http.post(EVAL, json={"questions": [A_QUESTION]})
        assert refused.status_code == 503
        assert refused.json()["detail"] == NO_MEMORY
