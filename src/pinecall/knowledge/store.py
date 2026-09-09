"""The knowledge base in Postgres: a push replaces a base whole, a search reads it two ways."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pinecall.knowledge.chunking import chunks_of
from pinecall.log.store import Pool
from pinecall.providers.embedder import Embedder, as_halfvec
from pinecall.types import (
    CANDIDATES_PER_BRANCH,
    Chunk,
    KnowledgeFile,
    reciprocal_rank_fusion,
    relative_to_the_best,
)
from pinecall.types.knowledge import DEFAULT_CHUNKS_PER_TURN

# How many texts one call to the embedder carries.
EMBED_BATCH = 32

# The BM25 index by the name 0009 gave it: pg_textsearch scores a text by one index's statistics
# and the query names it, the query first — `to_bm25query(<query>, <index>)`.
TEXT_INDEX = "knowledge_chunks_text_bm25"


@dataclass(frozen=True)
class Base:
    """One knowledge base as a listing shows it: its name, its chunks, when it was pushed."""

    base: str
    chunks: int
    pushed_at: datetime


# One statement, so a push is all or nothing: the base's row written or bumped, every chunk it
# had gone, every chunk it now has in. The vectors travel as text and become halfvec at the door,
# which keeps the driver out of the vector type entirely.
_PUT = """
WITH pushed AS (
    INSERT INTO knowledge_bases (org, base, model, dimensions, chunks, pushed_at)
        VALUES ($1, $2, $3, $4, $5, now())
        ON CONFLICT (org, base) DO UPDATE
        SET model = excluded.model, dimensions = excluded.dimensions,
            chunks = excluded.chunks, pushed_at = now()
), replaced AS (
    DELETE FROM knowledge_chunks WHERE org = $1 AND base = $2
)
INSERT INTO knowledge_chunks (org, base, path, heading, ordinal, text, embedding)
SELECT $1, $2, chunk.path, chunk.heading, chunk.ordinal, chunk.text, chunk.embedding::halfvec
FROM unnest($6::text[], $7::text[], $8::integer[], $9::text[], $10::text[])
    AS chunk (path, heading, ordinal, text, embedding)
"""

_BASES = "SELECT base, chunks, pushed_at FROM knowledge_bases WHERE org = $1 ORDER BY base"

# The chunks go with the row: 0009 declares them ON DELETE CASCADE. The row returned is the
# answer to "was there one", so dropping a name never pushed is told apart from dropping a base.
_DROP = "DELETE FROM knowledge_bases WHERE org = $1 AND base = $2 RETURNING base"

# The dense branch: nearest by cosine, the HNSW index's own order.
_NEAREST = """
SELECT id, path, heading, text
FROM knowledge_chunks
WHERE org = $1 AND base = $2
ORDER BY embedding <=> $3::halfvec
LIMIT $4
"""

# The words branch. `<@>` answers the negative BM25 score, lower is better, and 0 is a text
# none of the query's terms is in — which is not a candidate, so it never earns a rank.
_BEST_WORDED = f"""
SELECT id, path, heading, text
FROM (
    SELECT id, path, heading, text, text <@> to_bm25query($3, '{TEXT_INDEX}') AS score
    FROM knowledge_chunks
    WHERE org = $1 AND base = $2
) scored
WHERE score < 0
ORDER BY score
LIMIT $4
"""


class PgKnowledge:
    """A tenant's bases in Postgres: put, listed, dropped and searched, one org at a time."""

    def __init__(self, pool: Pool, embedder: Embedder) -> None:
        self._pool = pool
        self._embedder = embedder

    async def put(self, org: str, base: str, files: Sequence[KnowledgeFile]) -> int:
        """Replace the base with these files, chunked and embedded; how many chunks it became."""
        pieces = [piece for file in files for piece in chunks_of(file)]
        vectors = await self._embedded([piece.text for piece in pieces])
        await self._pool.execute(
            _PUT,
            org,
            base,
            await self._embedder.model(),
            self._embedder.dimensions,
            len(pieces),
            [piece.path for piece in pieces],
            [piece.heading for piece in pieces],
            [piece.ordinal for piece in pieces],
            [piece.text for piece in pieces],
            [as_halfvec(vector) for vector in vectors],
        )
        return len(pieces)

    async def bases(self, org: str) -> list[Base]:
        """Every base this org pushed, by name."""
        rows: Sequence[Mapping[str, Any]] = await self._pool.fetch(_BASES, org)
        return [
            Base(base=str(row["base"]), chunks=int(row["chunks"]), pushed_at=row["pushed_at"])
            for row in rows
        ]

    async def drop(self, org: str, base: str) -> bool:
        """Forget the base and its chunks. False when the org never pushed one by that name."""
        return await self._pool.fetchrow(_DROP, org, base) is not None

    async def search(
        self,
        org: str,
        base: str,
        query: str,
        *,
        k: int = DEFAULT_CHUNKS_PER_TURN,
        min_score: float | None = None,
    ) -> list[Chunk]:
        """The best k chunks for the query, by meaning and by words, fused; under min_score, cut."""
        [vector] = await self._embedder.embed([query])
        nearest, worded = await asyncio.gather(
            self._pool.fetch(_NEAREST, org, base, as_halfvec(vector), CANDIDATES_PER_BRANCH),
            self._pool.fetch(_BEST_WORDED, org, base, query, CANDIDATES_PER_BRANCH),
        )
        chunks = [
            _a_chunk(row, base, score)
            for row, score in _fused((nearest, worded))
            if min_score is None or score >= min_score
        ]
        return chunks[:k]

    async def _embedded(self, texts: Sequence[str]) -> list[list[float]]:
        """Every text's vector, asked of the embedder a batch at a time."""
        vectors: list[list[float]] = []
        for start in range(0, len(texts), EMBED_BATCH):
            vectors.extend(await self._embedder.embed(texts[start : start + EMBED_BATCH]))
        return vectors


# The fusion is types/fusion.py's, the same one memory ranks with: a candidate earns
# 1 / (k + rank) from each branch that lists it, and the sums are read against the best, so 1.0
# is the top chunk and a chunk one branch found near its top lands near a half.
def _fused(
    branches: Sequence[Sequence[Mapping[str, Any]]],
) -> list[tuple[Mapping[str, Any], float]]:
    """Every candidate of every branch with its fused score in 0..1, best first."""
    rows = {str(row["id"]): row for branch in branches for row in branch}
    fused = reciprocal_rank_fusion(*([str(row["id"]) for row in branch] for branch in branches))
    return [(rows[id], score) for id, score in relative_to_the_best(fused).items()]


def _a_chunk(row: Mapping[str, Any], base: str, score: float) -> Chunk:
    """One row back into the shape retrieval hands out."""
    return Chunk(
        id=str(row["id"]),
        base=base,
        path=str(row["path"]),
        heading=None if row["heading"] is None else str(row["heading"]),
        text=str(row["text"]),
        score=score,
    )
