"""Two files pushed, one turn filled, the sources read off the log: the whole chain in Postgres."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from functools import partial
from typing import Any, override

import pytest

from pinecall.filling import Filling, OpenCall
from pinecall.knowledge import PgKnowledge
from pinecall.log.store import MemoryStore, open_pool
from pinecall.log.writers import Logs
from pinecall.orgs.table import MemoryOrgs
from pinecall.orgs.vault import keys_brought_by
from pinecall.types import Docs, Org, Quotas, markers_in
from tests.filling.fakes import AGENT, CALL, OneCall, a_config, a_context, a_plan, the_tenants
from tests.knowledge.files import CLINICA, TARIFAS, an_org
from tests.postgres import Dev
from tests.vectors import HashEmbedder

pytestmark = pytest.mark.postgres

(RETRIEVED,) = markers_in('<!-- retrieved: {"k":2} -->')


# The same embedder every knowledge test uses, with a tally: a fill that answers nothing must
# not have paid a vendor for a vector on the way there.
class CountingEmbedder(HashEmbedder):
    """The hash embedder, counting the queries it was asked to embed."""

    def __init__(self) -> None:
        self.queries = 0

    @override
    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.queries += len(texts)
        return await super().embed(texts)


@pytest.fixture
def embedder() -> CountingEmbedder:
    """One embedder per test, so its tally is that test's own."""
    return CountingEmbedder()


@pytest.fixture
async def knowledge(postgres: Dev, embedder: CountingEmbedder) -> AsyncIterator[PgKnowledge]:
    """The real store on this run's schema, embedding with the hash of the words."""
    pool = await open_pool(postgres.dsn, schema=postgres.schema)
    try:
        yield PgKnowledge(pool, embedder)
    finally:
        await pool.close()


async def test_a_pushed_base_fills_a_retrieved_marker_and_the_log_names_the_sources(
    knowledge: PgKnowledge, raw_connection: Any
) -> None:
    org = await an_org(raw_connection)
    assert await knowledge.put(org, "clinica", [CLINICA, TARIFAS]) == 4
    store = MemoryStore()
    logs = Logs(store)
    logs.writing(CALL, AGENT)
    opened = OpenCall(org=org, context=a_context(), config=a_config(docs=Docs(base="clinica")))
    filling = Filling(
        None,
        knowledge,
        logs,
        OneCall(opened),
        partial(keys_brought_by, None),
        *a_plan(logs, the_tenants()),
    )

    query = "cuánto cuesta la revisión"
    fills = await filling.fill(CALL, query, [RETRIEVED], "sp_1")
    text = fills[RETRIEVED.line]
    assert text.startswith("### tarifas.md › Tarifas › Revisión\n")
    assert "cuarenta euros" in text

    [entry] = await store.since(CALL)
    assert (entry.type, entry.data["query"], entry.data["speech_id"]) == (
        "docs.sources",
        query,
        "sp_1",
    )
    sources = entry.data["sources"]
    assert len(sources) == 2
    assert sources[0]["path"] == "tarifas.md"
    assert sources[0]["heading"] == "Tarifas › Revisión"
    assert sources[0]["score"] == 1.0
    assert "cuarenta euros" in sources[0]["excerpt"]
    assert entry.data["took_ms"] >= 0


async def test_a_plan_that_keeps_no_chunks_fills_the_marker_with_nothing_and_embeds_nothing(
    knowledge: PgKnowledge, embedder: CountingEmbedder, raw_connection: Any
) -> None:
    """The base is right there in Postgres; the plan says the org has none, so nobody looks."""
    org = await an_org(raw_connection)
    assert await knowledge.put(org, "clinica", [CLINICA, TARIFAS]) == 4
    embedder.queries = 0
    store = MemoryStore()
    logs = Logs(store)
    logs.writing(CALL, AGENT)
    opened = OpenCall(org=org, context=a_context(), config=a_config(docs=Docs(base="clinica")))
    orgs = MemoryOrgs([Org(id=org, slug=org, name=org)])
    await orgs.set_quotas(org, Quotas(knowledge_chunks=0))
    filling = Filling(
        None, knowledge, logs, OneCall(opened), partial(keys_brought_by, None), *a_plan(logs, orgs)
    )

    fills = await filling.fill(CALL, "cuánto cuesta la revisión", [RETRIEVED], "sp_1")
    assert fills == {RETRIEVED.line: ""}
    assert embedder.queries == 0, "a fill that cannot use its answer never pays for one"
    assert await store.since(CALL) == [], "not an error, not an empty source list: nothing at all"
