"""The chunk cap on the real tables: the count is a query, and a refused push leaves no row."""

from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import httpx
import pytest

from pinecall.auth.keys import KeyRecord, MemoryKeys
from pinecall.knowledge import PgKnowledge
from pinecall.log.store import open_pool
from pinecall.orgs.table import MemoryOrgs
from pinecall.types import PRODUCTION, KnowledgeFile, Org, Quotas
from tests.api.conftest import A_KEY
from tests.knowledge.files import CLINICA, TARIFAS
from tests.postgres import Dev
from tests.vectors import HashEmbedder

pytestmark = pytest.mark.postgres

KNOWLEDGE = "/v1/knowledge"

# The two files cut into two chunks each, which is what tests/knowledge/test_store.py pins.
CHUNKS_OF_BOTH = 4


def pushed(*files: KnowledgeFile) -> dict[str, Any]:
    """The body `pinecall knowledge push` sends: the tenant's folder, path and text per file."""
    return {"files": [{"path": file.path, "text": file.text} for file in files]}


# The rows this module writes outlive the test in the run's schema, so every test pushes under an
# org of its own and the counts it asserts can only be its own.
@pytest.fixture
def org() -> str:
    """A tenant nobody else in this schema has pushed for."""
    return f"org-{uuid4().hex[:12]}"


@pytest.fixture
async def a_row_for_the_org(raw_connection: Any, org: str) -> None:
    """The orgs row knowledge_bases references: without it a push is a foreign key error."""
    await raw_connection.execute("insert into orgs (id, slug, name) values ($1, $1, $1)", org)


@pytest.fixture
def keys(org: str) -> MemoryKeys:
    """The tenant's key, issued to this test's own org: every door reads the org off it."""
    return MemoryKeys({A_KEY: KeyRecord(key_id="k_1", org=org)})


@pytest.fixture
def orgs(org: str) -> MemoryOrgs:
    """The tenants this gateway knows, so a quota can be set on the one that pushes."""
    return MemoryOrgs([Org(id=org, slug=org, name=org)])


@pytest.fixture
async def knowledge(
    postgres: Dev,
    a_row_for_the_org: None,  # noqa: ARG001 — the FK has to exist before a push runs
) -> AsyncIterator[PgKnowledge]:
    """The real store on this run's schema, embedding with the hash of the words."""
    pool = await open_pool(postgres.dsn, schema=postgres.schema)
    try:
        yield PgKnowledge(pool, HashEmbedder())
    finally:
        await pool.close()


async def test_a_push_past_the_cap_answers_the_sentence_and_leaves_the_table_as_it_was(
    tenant_http: httpx.AsyncClient, knowledge: PgKnowledge, orgs: MemoryOrgs, org: str
) -> None:
    """The org's other bases are counted; the push is judged whole and refused before it writes."""
    await orgs.set_quotas(org, Quotas(knowledge_chunks=CHUNKS_OF_BOTH))
    landed = await tenant_http.put(f"{KNOWLEDGE}/clinica", json=pushed(CLINICA, TARIFAS))
    assert (landed.status_code, landed.json()["chunks"]) == (200, CHUNKS_OF_BOTH)

    refused = await tenant_http.put(f"{KNOWLEDGE}/tarifas", json=pushed(TARIFAS))
    assert refused.status_code == 429
    assert refused.json()["detail"] == (
        f"org {org} has used 6 of its 4 knowledge_chunks: credits.exhausted"
    )
    assert [(one.base, one.chunks) for one in await knowledge.bases(org, PRODUCTION)] == [
        ("clinica", 4)
    ]
    assert await knowledge.kept(org) == CHUNKS_OF_BOTH


async def test_pushing_the_same_base_again_at_the_cap_is_a_replacement_and_not_a_second_copy(
    tenant_http: httpx.AsyncClient, knowledge: PgKnowledge, orgs: MemoryOrgs, org: str
) -> None:
    """What the base holds today is freed by the very push being judged, so it fits exactly."""
    await orgs.set_quotas(org, Quotas(knowledge_chunks=CHUNKS_OF_BOTH))
    body = pushed(CLINICA, TARIFAS)
    assert (await tenant_http.put(f"{KNOWLEDGE}/clinica", json=body)).status_code == 200
    again = await tenant_http.put(f"{KNOWLEDGE}/clinica", json=body)
    assert again.status_code == 200, again.text
    assert await knowledge.kept(org) == CHUNKS_OF_BOTH


async def test_a_plan_that_keeps_no_chunks_never_writes_a_base_row_at_all(
    tenant_http: httpx.AsyncClient, knowledge: PgKnowledge, orgs: MemoryOrgs, org: str
) -> None:
    await orgs.set_quotas(org, Quotas(knowledge_chunks=0))
    refused = await tenant_http.put(f"{KNOWLEDGE}/clinica", json=pushed(CLINICA))
    assert refused.status_code == 429
    assert refused.json()["detail"] == (
        f"org {org} has used 2 of its 0 knowledge_chunks: credits.exhausted"
    )
    assert await knowledge.bases(org, PRODUCTION) == []
    assert await knowledge.kept(org) == 0
