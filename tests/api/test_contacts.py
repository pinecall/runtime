"""A contact's memory as the tenant reads it, and as the contact has it erased."""

from __future__ import annotations

import httpx
import pytest

from pinecall.api._deps import NO_MEMORY
from tests.filling.fakes import LEARNED, ScriptedMemory, a_fact

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
        assert NO_MEMORY == "this gateway keeps no memory: it runs on a dev key"
