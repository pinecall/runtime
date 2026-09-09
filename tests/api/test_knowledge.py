"""The knowledge base's three doors, on the org's key: a push, the list, a drop."""

from __future__ import annotations

import httpx
import pytest

from pinecall.api._deps import NO_KNOWLEDGE
from tests.filling.fakes import ScriptedKnowledge

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
        assert NO_KNOWLEDGE == "this gateway keeps no knowledge: it runs on a dev key"
