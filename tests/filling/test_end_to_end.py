"""Two files pushed, one turn filled, the sources read off the log: the whole chain in Postgres."""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import partial
from typing import Any

import pytest

from pinecall.filling import Filling, OpenCall
from pinecall.knowledge import PgKnowledge
from pinecall.log.store import MemoryStore, open_pool
from pinecall.log.writers import Logs
from pinecall.orgs.vault import keys_brought_by
from pinecall.types import Docs, markers_in
from tests.filling.fakes import AGENT, CALL, OneCall, a_config, a_context
from tests.knowledge.files import CLINICA, TARIFAS, an_org
from tests.postgres import Dev
from tests.vectors import HashEmbedder

pytestmark = pytest.mark.postgres

(RETRIEVED,) = markers_in('<!-- retrieved: {"k":2} -->')


@pytest.fixture
async def knowledge(postgres: Dev) -> AsyncIterator[PgKnowledge]:
    """The real store on this run's schema, embedding with the hash of the words."""
    pool = await open_pool(postgres.dsn, schema=postgres.schema)
    try:
        yield PgKnowledge(pool, HashEmbedder())
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
    filling = Filling(None, knowledge, logs, OneCall(opened), partial(keys_brought_by, None))

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
