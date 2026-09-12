"""The knowledge base in Postgres: a push replaces a base whole, a search reads it two ways."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pinecall.knowledge.chunking import chunks_of
from pinecall.log.store import Pool
from pinecall.providers.embedder import Embedder, WrongModel, as_halfvec
from pinecall.types import (
    CANDIDATES_PER_BRANCH,
    Chunk,
    Env,
    KnowledgeFile,
    reciprocal_rank_fusion,
    relative_to_the_best,
)
from pinecall.types.knowledge import DEFAULT_CHUNKS_PER_TURN

# What a search says when the vectors in the table and the vectors this gateway makes came out of
# two different models: they are numbers of the same width and nothing else, and ranking one
# against the other is a plausible answer with no meaning in it. The way out is in the sentence.
PUSHED_WITH_ANOTHER_MODEL = (
    "base {base} was pushed with {pushed}; this gateway embeds with {mine}: push it again"
)

# The BM25 index by the name 0009 gave it: pg_textsearch scores a text by one index's statistics
# and the query names it, the query first — `to_bm25query(<query>, <index>)`.
TEXT_INDEX = "knowledge_chunks_text_bm25"


@dataclass(frozen=True)
class Base:
    """One knowledge base as a listing shows it: its name, its chunks, when it was pushed."""

    base: str
    chunks: int
    # Which embedder wrote this base's vectors. A listing that left it out was a listing where a
    # tenant learned of a mismatch from a 409 at the next turn instead of from the list itself.
    model: str
    pushed_at: datetime


# One statement, so a push is all or nothing: the base's row written or bumped, every chunk it
# had gone, every chunk it now has in. The vectors travel as text and become halfvec at the door,
# which keeps the driver out of the vector type entirely.
_PUT = """
WITH pushed AS (
    INSERT INTO knowledge_bases (org, env, base, model, dimensions, chunks, pushed_at)
        VALUES ($1, $2, $3, $4, $5, $6, now())
        ON CONFLICT (org, env, base) DO UPDATE
        SET model = excluded.model, dimensions = excluded.dimensions,
            chunks = excluded.chunks, pushed_at = now()
), replaced AS (
    DELETE FROM knowledge_chunks WHERE org = $1 AND env = $2 AND base = $3
)
INSERT INTO knowledge_chunks (org, env, base, path, heading, ordinal, text, embedding)
SELECT $1, $2, $3, chunk.path, chunk.heading, chunk.ordinal, chunk.text, chunk.embedding::halfvec
FROM unnest($7::text[], $8::text[], $9::integer[], $10::text[], $11::text[])
    AS chunk (path, heading, ordinal, text, embedding)
"""

_BASES = """
SELECT base, chunks, model, pushed_at FROM knowledge_bases
WHERE org = $1 AND env = $2 ORDER BY base
"""

# What the org KEEPS across every base of BOTH worlds, which is what its quota is about: a chunk
# a laptop pushed is a row on the same disk as one the box pushed. The base's own row already
# counts its chunks, so this is a sum over one index and not a scan of the chunks. What a push
# about to replace a base would free is the door's arithmetic, not this query's — the door already
# holds that base's row from the listing it drew.
_KEPT = "SELECT coalesce(sum(chunks), 0) AS kept FROM knowledge_bases WHERE org = $1"

# Which model wrote this base's vectors. None when the org pushed no base by that name, which is
# not an error here: a search of a base nobody pushed answers with nothing, as it always did.
_MODEL_OF = "SELECT model FROM knowledge_bases WHERE org = $1 AND env = $2 AND base = $3"

# The chunks go with the row: 0009 declares them ON DELETE CASCADE. The row returned is the
# answer to "was there one", so dropping a name never pushed is told apart from dropping a base.
_DROP = "DELETE FROM knowledge_bases WHERE org = $1 AND env = $2 AND base = $3 RETURNING base"

# The dense branch: nearest by cosine, the HNSW index's own order.
_NEAREST = """
SELECT id, path, heading, text
FROM knowledge_chunks
WHERE org = $1 AND env = $2 AND base = $3
ORDER BY embedding <=> $4::halfvec
LIMIT $5
"""

# The words branch. `<@>` answers the negative BM25 score, lower is better, and 0 is a text
# none of the query's terms is in — which is not a candidate, so it never earns a rank.
_BEST_WORDED = f"""
SELECT id, path, heading, text
FROM (
    SELECT id, path, heading, text, text <@> to_bm25query($4, '{TEXT_INDEX}') AS score
    FROM knowledge_chunks
    WHERE org = $1 AND env = $2 AND base = $3
) scored
WHERE score < 0
ORDER BY score
LIMIT $5
"""


class PgKnowledge:
    """A tenant's bases in Postgres: put, listed, dropped and searched, one org at a time."""

    def __init__(self, pool: Pool, embedder: Embedder) -> None:
        self._pool = pool
        self._embedder = embedder

    # One document per FILE, because that is what a document is here: the embedder is handed a
    # file's chunks together, and a contextual model then embeds each one seeing its neighbours —
    # a tariff line finds its own heading's words even when the line itself does not carry them.
    # How a document is windowed to fit a model's context is the embedder's business, never this
    # table's: the store hands over the shape and reads back the same shape.
    async def put(self, org: str, env: Env, base: str, files: Sequence[KnowledgeFile]) -> int:
        """Replace the base with these files, chunked and embedded; how many chunks it became."""
        cut = [chunks_of(file) for file in files]
        embedded = await self._embedder.embed_documents(
            [[piece.text for piece in file] for file in cut]
        )
        pieces = [piece for file in cut for piece in file]
        vectors = [vector for file in embedded for vector in file]
        await self._pool.execute(
            _PUT,
            org,
            env,
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

    async def bases(self, org: str, env: Env) -> list[Base]:
        """Every base this org pushed in this world, by name."""
        rows: Sequence[Mapping[str, Any]] = await self._pool.fetch(_BASES, org, env)
        return [
            Base(
                base=str(row["base"]),
                chunks=int(row["chunks"]),
                model=str(row["model"]),
                pushed_at=row["pushed_at"],
            )
            for row in rows
        ]

    async def drop(self, org: str, env: Env, base: str) -> bool:
        """Forget the base and its chunks. False when the org never pushed one by that name."""
        return await self._pool.fetchrow(_DROP, org, env, base) is not None

    async def kept(self, org: str) -> int:
        """One sum over the base rows: every chunk this org holds, in both worlds."""
        row = await self._pool.fetchrow(_KEPT, org)
        return 0 if row is None else int(row["kept"])

    # The cut is a pass of regexes over the tenant's own Markdown and runs twice on a push: once
    # to say how big it would be, once to write it. The alternative is a `put` that reads quotas,
    # which would put admission inside the table.
    def how_many_chunks(self, files: Sequence[KnowledgeFile]) -> int:
        """The same cut a push makes, counted: what the quota judges the push by."""
        return sum(len(chunks_of(file)) for file in files)

    async def search(
        self,
        org: str,
        env: Env,
        base: str,
        query: str,
        *,
        k: int = DEFAULT_CHUNKS_PER_TURN,
        min_score: float | None = None,
    ) -> list[Chunk]:
        """The best k chunks for the query, by meaning and by words, fused; under min_score, cut."""
        [vector] = await self._embedder.embed([query])
        # The base's row rides along with the two branches instead of gating them: it is one more
        # index read on a primary key, and the turn's budget covers the slowest of the three
        # rather than their sum. The refusal comes before a rank is read, either way.
        pushed, nearest, worded = await asyncio.gather(
            self._pool.fetchrow(_MODEL_OF, org, env, base),
            self._pool.fetch(_NEAREST, org, env, base, as_halfvec(vector), CANDIDATES_PER_BRANCH),
            self._pool.fetch(_BEST_WORDED, org, env, base, query, CANDIDATES_PER_BRANCH),
        )
        _the_same_model(base, pushed, await self._embedder.model())
        chunks = [
            _a_chunk(row, base, score)
            for row, score in _fused((nearest, worded))
            if min_score is None or score >= min_score
        ]
        return chunks[:k]


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


def _the_same_model(base: str, pushed: Mapping[str, Any] | None, mine: str) -> None:
    """Refuse a base whose vectors another model wrote, naming both models and the way out."""
    if pushed is not None and str(pushed["model"]) != mine:
        raise WrongModel(
            PUSHED_WITH_ANOTHER_MODEL.format(base=base, pushed=str(pushed["model"]), mine=mine)
        )


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
