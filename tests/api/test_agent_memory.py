"""Memory across callers: an agent's current facts, a page at a time, and one fact forgotten."""

from __future__ import annotations

from uuid import uuid4

import httpx
import pytest

from pinecall.api._deps import NO_MEMORY
from pinecall.api.agent_memory import NO_SUCH_FACT
from pinecall.auth.keys import NOT_OPENED, KeyRecord, MemoryKeys
from pinecall.memory import Memory
from tests.api.conftest import A_KEY, A_RECORD, AGENT, over_the_asgi_app
from tests.lookups.fakes import ScriptedMemory, a_fact

pytestmark = pytest.mark.unit

TAUGHT = f"/v1/agents/{AGENT}/memory"
ONE = str(uuid4())
TWO = str(uuid4())
A_READER_KEY = "pk_test_reads_calls"
A_READER = KeyRecord(key_id="k_read", org=A_RECORD.org, scopes=frozenset({"calls"}))


@pytest.fixture
def keys() -> MemoryKeys:
    return MemoryKeys({A_KEY: A_RECORD, A_READER_KEY: A_READER})


@pytest.fixture
def memory() -> ScriptedMemory:
    return ScriptedMemory(
        answers=[a_fact(ONE, "prefiere la mañana"), a_fact(TWO, "tiene un perro")]
    )


async def test_the_agents_facts_are_listed_a_page_at_a_time(tenant_http: httpx.AsyncClient) -> None:
    first = (await tenant_http.get(f"{TAUGHT}?limit=1")).json()
    assert [fact["text"] for fact in first["facts"]] == ["prefiere la mañana"]
    assert set(first["facts"][0]) == {"id", "contact", "text", "category", "written_at"}
    rest = (await tenant_http.get(f"{TAUGHT}?limit=1&after={first['next']}")).json()
    assert ([fact["id"] for fact in rest["facts"]], rest["next"]) == ([TWO], None)
    assert [f["id"] for f in (await tenant_http.get(f"{TAUGHT}?q=perro")).json()["facts"]] == [TWO]


async def test_one_fact_is_forgotten_and_a_second_time_there_is_nothing_to_forget(
    tenant_http: httpx.AsyncClient,
) -> None:
    forgotten = await tenant_http.delete(f"/v1/memory/facts/{ONE}")
    assert (forgotten.status_code, forgotten.json()) == (200, {"forgotten": 1})
    again = await tenant_http.delete(f"/v1/memory/facts/{ONE}")
    assert (again.status_code, again.json()["detail"]) == (404, NO_SUCH_FACT.format(id=ONE))
    assert [f["id"] for f in (await tenant_http.get(TAUGHT)).json()["facts"]] == [TWO]
    assert (await tenant_http.delete("/v1/memory/facts/not-an-id")).status_code == 422


async def test_a_key_without_memory_is_refused(
    wired: None,  # noqa: ARG001
) -> None:
    reader = over_the_asgi_app(f"Bearer {A_READER_KEY}")
    try:
        refused = await reader.get(TAUGHT)
    finally:
        await reader.aclose()
    assert refused.json()["detail"] == NOT_OPENED.format(scope="memory", opens="calls")


@pytest.mark.parametrize("memory", [None])
async def test_a_gateway_with_no_memory_says_so(
    tenant_http: httpx.AsyncClient,
    memory: Memory | None,  # noqa: ARG001
) -> None:
    answered = await tenant_http.get(TAUGHT)
    assert (answered.status_code, answered.json()["detail"]) == (503, NO_MEMORY)
