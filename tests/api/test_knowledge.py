"""The knowledge base's three doors, on the org's key: a push, the list, a drop."""

from __future__ import annotations

import httpx
import pytest

from pinecall.api._deps import NO_KNOWLEDGE
from pinecall.orgs.table import MemoryOrgs
from pinecall.types import Chunk, Quotas
from tests.api.conftest import A_RECORD
from tests.lookups.fakes import ScriptedKnowledge

pytestmark = pytest.mark.unit

KNOWLEDGE = "/v1/knowledge"
A_PUSH = {"files": [{"path": "tarifas.md", "text": "# Tarifas\n\nRevisión: 45 €."}]}


@pytest.fixture
def knowledge() -> ScriptedKnowledge:
    """A base nobody has pushed to yet."""
    return ScriptedKnowledge()


async def test_a_push_then_the_list_then_a_drop(
    tenant_http: httpx.AsyncClient, knowledge: ScriptedKnowledge
) -> None:
    pushed = await tenant_http.put(f"{KNOWLEDGE}/clinica", json=A_PUSH)
    assert pushed.status_code == 200
    body = pushed.json()
    assert (body["base"], body["chunks"]) == ("clinica", 2)
    assert body["took_ms"] >= 0
    assert [file.path for file in knowledge.pushed["clinica"]] == ["tarifas.md"]

    listed = await tenant_http.get(KNOWLEDGE)
    assert listed.status_code == 200
    [base] = listed.json()["bases"]
    assert (base["base"], base["chunks"]) == ("clinica", 2)
    assert isinstance(base["pushed_at"], float)

    assert (await tenant_http.delete(f"{KNOWLEDGE}/clinica")).status_code == 204
    assert (await tenant_http.get(KNOWLEDGE)).json() == {"bases": []}


async def test_dropping_a_base_nobody_pushed_is_a_refusal_that_names_it(
    tenant_http: httpx.AsyncClient,
) -> None:
    refused = await tenant_http.delete(f"{KNOWLEDGE}/nadie")
    assert refused.status_code == 404
    assert (
        refused.json()["detail"]
        == "no knowledge base named nadie: nothing was pushed under that name"
    )


async def test_the_doors_take_the_orgs_key_and_nothing_else(
    tenant_http: httpx.AsyncClient,
) -> None:
    refused = await tenant_http.get(KNOWLEDGE, headers={"Authorization": "Bearer pk_nobody"})
    assert refused.status_code == 401


class TestOnADevKey:
    """No Postgres, no tables: every door says so in one sentence, and 503 is the number."""

    @pytest.fixture
    def knowledge(self) -> None:
        return None

    async def test_a_push_a_list_and_a_drop_all_answer_the_same_sentence(
        self, tenant_http: httpx.AsyncClient
    ) -> None:
        pushed = await tenant_http.put(f"{KNOWLEDGE}/clinica", json=A_PUSH)
        listed = await tenant_http.get(KNOWLEDGE)
        dropped = await tenant_http.delete(f"{KNOWLEDGE}/clinica")
        for answer in (pushed, listed, dropped):
            assert answer.status_code == 503
            assert answer.json()["detail"] == NO_KNOWLEDGE
        assert "no database answered" in NO_KNOWLEDGE
        assert "migrate up" in NO_KNOWLEDGE


# ── what the plan allows ────────────────────────────────────────────────────────


class TestWhenThePlanCapsTheChunks:
    """knowledge_chunks is a stock: the push is judged whole, before a single row is written."""

    async def limited(self, orgs: MemoryOrgs, chunks: int | None) -> None:
        """The org's plan, as the operator's door sets it: the whole set, replaced."""
        await orgs.set_quotas(A_RECORD.org, Quotas(knowledge_chunks=chunks))

    async def test_a_push_that_fits_lands_and_one_chunk_more_is_refused_with_both_numbers(
        self, tenant_http: httpx.AsyncClient, knowledge: ScriptedKnowledge, orgs: MemoryOrgs
    ) -> None:
        await self.limited(orgs, 2)
        assert (await tenant_http.put(f"{KNOWLEDGE}/clinica", json=A_PUSH)).status_code == 200
        refused = await tenant_http.put(f"{KNOWLEDGE}/tarifas", json=A_PUSH)
        assert refused.status_code == 429
        assert refused.json()["detail"] == (
            "org clinica has used 4 of its 2 knowledge_chunks: credits.exhausted"
        )
        assert list(knowledge.pushed) == ["clinica"], "the refused push wrote nothing"

    async def test_pushing_a_base_again_frees_what_it_held_so_a_replacement_is_not_a_second_copy(
        self, tenant_http: httpx.AsyncClient, orgs: MemoryOrgs
    ) -> None:
        """A push replaces the base whole, so its own chunks are never counted twice."""
        await self.limited(orgs, 2)
        assert (await tenant_http.put(f"{KNOWLEDGE}/clinica", json=A_PUSH)).status_code == 200
        again = await tenant_http.put(f"{KNOWLEDGE}/clinica", json=A_PUSH)
        assert again.status_code == 200, again.text

    async def test_a_plan_that_keeps_no_chunks_refuses_the_first_push_in_a_sentence(
        self, tenant_http: httpx.AsyncClient, knowledge: ScriptedKnowledge, orgs: MemoryOrgs
    ) -> None:
        """Zero is how a free plan has no knowledge base at all, and it says so at the door."""
        await self.limited(orgs, 0)
        refused = await tenant_http.put(f"{KNOWLEDGE}/clinica", json=A_PUSH)
        assert refused.status_code == 429
        assert refused.json()["detail"] == (
            "org clinica has used 2 of its 0 knowledge_chunks: credits.exhausted"
        )
        assert knowledge.pushed == {}

    async def test_an_org_nobody_limited_pushes_whatever_it_likes(
        self, tenant_http: httpx.AsyncClient, orgs: MemoryOrgs
    ) -> None:
        """NULL is what a self-hosted box has, because it never sets a row."""
        await self.limited(orgs, None)
        for base in ("clinica", "tarifas", "horarios"):
            assert (await tenant_http.put(f"{KNOWLEDGE}/{base}", json=A_PUSH)).status_code == 200

    async def test_a_drop_and_the_listing_are_never_refused_by_a_quota(
        self, tenant_http: httpx.AsyncClient, orgs: MemoryOrgs
    ) -> None:
        """A cap is about what is kept: reading it and giving it back must always work."""
        await self.limited(orgs, None)
        await tenant_http.put(f"{KNOWLEDGE}/clinica", json=A_PUSH)
        await self.limited(orgs, 0)
        assert (await tenant_http.get(KNOWLEDGE)).status_code == 200
        assert (await tenant_http.delete(f"{KNOWLEDGE}/clinica")).status_code == 204


# A golden is the only thing that can say the index missed a BETTER passage: the judge that runs on
# every call can only weigh what the model was given. docs/retrieval/spec.md.
async def test_a_base_that_answers_every_question_first_scores_one_on_both(
    tenant_http: httpx.AsyncClient, knowledge: ScriptedKnowledge
) -> None:
    knowledge.answers = [_a_chunk("tarifas.md", "Tarifas › Revisión")]
    await tenant_http.put(f"{KNOWLEDGE}/clinica", json=A_PUSH)
    said = (
        await tenant_http.post(
            f"{KNOWLEDGE}/clinica/eval",
            json={"questions": [{"asks": "la revisión", "expects": "tarifas.md"}]},
        )
    ).json()
    assert (said["questions"], said["recall_at_k"], said["ndcg_at_10"]) == (1, 1.0, 1.0)
    assert said["misses"] == []
    assert said["model"]


async def test_a_question_the_base_misses_comes_back_with_what_it_found_instead(
    tenant_http: httpx.AsyncClient, knowledge: ScriptedKnowledge
) -> None:
    knowledge.answers = [_a_chunk("horarios.md", "Horario de consulta")]
    await tenant_http.put(f"{KNOWLEDGE}/clinica", json=A_PUSH)
    said = (
        await tenant_http.post(
            f"{KNOWLEDGE}/clinica/eval",
            json={"questions": [{"asks": "la revisión", "expects": "tarifas.md"}]},
        )
    ).json()
    assert said["recall_at_k"] == 0.0
    (missed,) = said["misses"]
    assert missed["expects"] == "tarifas.md"
    assert missed["found"] == ["horarios.md › Horario de consulta"]


async def test_a_golden_against_a_base_nobody_pushed_is_the_refusal_a_drop_answers(
    tenant_http: httpx.AsyncClient,
) -> None:
    refused = await tenant_http.post(
        f"{KNOWLEDGE}/nadie/eval", json={"questions": [{"asks": "x", "expects": "y"}]}
    )
    assert refused.status_code == 404
    assert "nadie" in refused.json()["detail"]


def _a_chunk(path: str, heading: str) -> Chunk:
    """One chunk as a search hands it back; where it came from is all a golden reads."""
    return Chunk(id="c", base="clinica", path=path, heading=heading, text="…", score=1.0)
