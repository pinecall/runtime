"""Every fact another embedder wrote, embedded again from its own text by the one this box runs."""

from __future__ import annotations

from pinecall.log.store import Pool
from pinecall.providers.embedder import Embedder, halfvec_literal

# A fact's vector is an index over its text, never the fact: the text, the category, the dates and
# the chain of what superseded what are untouched, which is why this is the one UPDATE of a row
# besides `invalidated_at`. Recall already compares a vector only with vectors of the same model
# (pgvector.py), so a fact another model wrote is found by BM25 alone until it is written again.
_STALE = "SELECT id, text FROM contact_memories WHERE model <> $1 ORDER BY created_at, id"

_WRITE = """
UPDATE contact_memories SET embedding = $2::text::halfvec, model = $3
WHERE id = $1 AND model <> $3
"""

# How many facts go to the embedder in one request: a fact is a sentence, and a failed batch
# leaves the ones before it written and the rest as they were, to be picked up by the next run.
# A batch is one transaction and one round trip, so the table never says half of one.
BATCH = 64


async def reembedded(pool: Pool, embedder: Embedder, *, batch: int = BATCH) -> int:
    """How many facts were written again. Running it twice writes nothing the second time."""
    model = await embedder.model()
    stale = list(await pool.fetch(_STALE, model))
    for start in range(0, len(stale), batch):
        rows = stale[start : start + batch]
        vectors = await embedder.embed([row["text"] for row in rows])
        written = [
            (row["id"], halfvec_literal(vector), model)
            for row, vector in zip(rows, vectors, strict=True)
        ]
        async with pool.acquire() as connection, connection.transaction():
            await connection.executemany(_WRITE, written)
    return len(stale)
