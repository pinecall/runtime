"""The knowledge base in Postgres: a push replaces a base whole, a search reads it two ways."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pinecall.knowledge import files as the_files
from pinecall.knowledge.chunking import chunks_of
from pinecall.knowledge.files import File
from pinecall.log.store import Pool
from pinecall.providers.embedder import Embedder, WrongModel, as_halfvec
from pinecall.types import (
    CANDIDATES_PER_BRANCH,
    Chunk,
    Env,
    KnowledgeFile,
    reciprocal_rank_fusion,
    relative_to_the_best,
    whose,
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
# had gone, every chunk it now has in, and the files kept as they arrived (0041) — a file the
# folder no longer has goes, one it still has is replaced in place, so no path is deleted and
# inserted in the same statement. The vectors travel as text and become halfvec at the door,
# which keeps the driver out of the vector type entirely.
_PUT = """
WITH pushed AS (
    INSERT INTO knowledge_bases (org, env, holder, base, model, dimensions, chunks, pushed_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, now())
        ON CONFLICT (org, env, holder, base) DO UPDATE
        SET model = excluded.model, dimensions = excluded.dimensions,
            chunks = excluded.chunks, pushed_at = now()
), replaced AS (
    DELETE FROM knowledge_chunks WHERE org = $1 AND env = $2 AND holder = $3 AND base = $4
), forgotten AS (
    DELETE FROM knowledge_files
    WHERE org = $1 AND env = $2 AND holder = $3 AND base = $4 AND path <> ALL ($13::text[])
), filed AS (
    INSERT INTO knowledge_files (org, env, holder, base, path, text, chunks, pushed_at)
    SELECT $1, $2, $3, $4, file.path, file.text, file.chunks, now()
    FROM unnest($13::text[], $14::text[], $15::integer[]) AS file (path, text, chunks)
    ON CONFLICT (org, env, holder, base, path) DO UPDATE
    SET text = excluded.text, chunks = excluded.chunks, pushed_at = now()
)
INSERT INTO knowledge_chunks (org, env, holder, base, path, heading, ordinal, text, embedding)
SELECT $1, $2, $3, $4, chunk.path, chunk.heading, chunk.ordinal, chunk.text,
       chunk.embedding::halfvec
FROM unnest($8::text[], $9::text[], $10::integer[], $11::text[], $12::text[])
    AS chunk (path, heading, ordinal, text, embedding)
"""

# Yours, and the org's own for a name you have not pushed: a developer who has pushed nothing
# reads what the team wrote down, the way `Registry.of()` falls back to the org's corner. Nobody
# joins a team to an empty knowledge base. DISTINCT ON takes yours where both exist, because ''
# sorts before any member id and DESC puts yours first.
_BASES = """
SELECT DISTINCT ON (base) base, chunks, model, pushed_at FROM knowledge_bases
WHERE org = $1 AND env = $2 AND holder IN ($3, '')
ORDER BY base, holder DESC
"""

# What the org KEEPS across every base of BOTH worlds, which is what its quota is about: a chunk
# a laptop pushed is a row on the same disk as one the box pushed. The base's own row already
# counts its chunks, so this is a sum over one index and not a scan of the chunks. What a push
# about to replace a base would free is the door's arithmetic, not this query's — the door already
# holds that base's row from the listing it drew.
_KEPT = "SELECT coalesce(sum(chunks), 0) AS kept FROM knowledge_bases WHERE org = $1"

# Which model wrote this base's vectors. None when the org pushed no base by that name, which is
# not an error here: a search of a base nobody pushed answers with nothing, as it always did.
_MODEL_OF = """
SELECT model FROM knowledge_bases
WHERE org = $1 AND env = $2 AND holder IN ($3, '') AND base = $4
ORDER BY holder DESC LIMIT 1
"""

# Whose copy of this base a read is about: yours when you pushed one, the org's otherwise. Written
# once and pasted into the two branch queries, so neither can drift from `_MODEL_OF`.
_WHOSE = """
    holder = (SELECT holder FROM knowledge_bases
              WHERE org = $1 AND env = $2 AND holder IN ($3, '') AND base = $4
              ORDER BY holder DESC LIMIT 1)
"""

# The chunks go with the row: 0009 declares them ON DELETE CASCADE. The row returned is the
# answer to "was there one", so dropping a name never pushed is told apart from dropping a base.
# YOUR copy and never the org's: a `knowledge drop` on a laptop must not take the base the team —
# or the telephone — reads. Dropping a name you never pushed is the same answer as dropping one
# nobody did, even where the org has one.
_DROP = """
DELETE FROM knowledge_bases WHERE org = $1 AND env = $2 AND holder = $3 AND base = $4
RETURNING base
"""

# The dense branch: nearest by cosine, the HNSW index's own order.
_NEAREST = f"""
SELECT id, path, heading, text
FROM knowledge_chunks
WHERE org = $1 AND env = $2 AND base = $4 AND {_WHOSE}
ORDER BY embedding <=> $5::halfvec
LIMIT $6
"""

# The words branch. `<@>` answers the negative BM25 score, lower is better, and 0 is a text
# none of the query's terms is in — which is not a candidate, so it never earns a rank.
_BEST_WORDED = f"""
SELECT id, path, heading, text
FROM (
    SELECT id, path, heading, text, text <@> to_bm25query($5, '{TEXT_INDEX}') AS score
    FROM knowledge_chunks
    WHERE org = $1 AND env = $2 AND base = $4 AND {_WHOSE}
) scored
WHERE score < 0
ORDER BY score
LIMIT $6
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
    async def put(
        self, org: str, env: Env, holder: str | None, base: str, files: Sequence[KnowledgeFile]
    ) -> int:
        """Replace THIS corner's base with these files; how many chunks it became."""
        cut = [chunks_of(file) for file in files]
        embedded = await self._embedder.embed_documents(
            [[piece.text for piece in file] for file in cut]
        )
        pieces = [piece for file in cut for piece in file]
        vectors = [vector for file in embedded for vector in file]
        paths, texts, counted = the_files.as_columns(files)
        await self._pool.execute(
            _PUT,
            org,
            env,
            whose(holder),
            base,
            await self._embedder.model(),
            self._embedder.dimensions,
            len(pieces),
            [piece.path for piece in pieces],
            [piece.heading for piece in pieces],
            [piece.ordinal for piece in pieces],
            [piece.text for piece in pieces],
            [as_halfvec(vector) for vector in vectors],
            paths,
            texts,
            counted,
        )
        return len(pieces)

    # The files of a base, one at a time — what a person at the console reads and edits. Each verb
    # is knowledge/files.py's, over this store's pool and embedder; the base stays the unit a push
    # replaces and a drop forgets.
    async def files(self, org: str, env: Env, holder: str | None, base: str) -> list[File]:
        """Every file of the base this corner reads, by path, without their text."""
        return await the_files.files(self._pool, org, env, holder, base)

    async def file(
        self, org: str, env: Env, holder: str | None, base: str, path: str
    ) -> File | None:
        """One file of the base this corner reads, text and all; None when there is none."""
        return await the_files.file(self._pool, org, env, holder, base, path)

    async def freed_by(self, org: str, env: Env, holder: str | None, base: str, path: str) -> int:
        """How many chunks this corner's own copy of the file holds: what putting it frees."""
        return await the_files.freed_by(self._pool, org, env, holder, base, path)

    async def put_file(
        self, org: str, env: Env, holder: str | None, base: str, file: KnowledgeFile
    ) -> int:
        """This corner's copy of one file replaced, or the base begun with it; how many chunks."""
        return await the_files.put_file(self._pool, self._embedder, org, env, holder, base, file)

    async def drop_file(self, org: str, env: Env, holder: str | None, base: str, path: str) -> bool:
        """This corner's copy of one file gone, and the base with it when it was the last."""
        return await the_files.drop_file(self._pool, org, env, holder, base, path)

    async def bases(self, org: str, env: Env, holder: str | None = None) -> list[Base]:
        """Every base this corner can read in this world: its own, and the org's for a name it
        has not pushed."""
        rows: Sequence[Mapping[str, Any]] = await self._pool.fetch(_BASES, org, env, whose(holder))
        return [
            Base(
                base=str(row["base"]),
                chunks=int(row["chunks"]),
                model=str(row["model"]),
                pushed_at=row["pushed_at"],
            )
            for row in rows
        ]

    async def drop(self, org: str, env: Env, holder: str | None, base: str) -> bool:
        """Forget THIS corner's base and its chunks. False when it pushed none by that name."""
        return await self._pool.fetchrow(_DROP, org, env, whose(holder), base) is not None

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
        holder: str | None,
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
        mine = whose(holder)
        pushed, nearest, worded = await asyncio.gather(
            self._pool.fetchrow(_MODEL_OF, org, env, mine, base),
            self._pool.fetch(
                _NEAREST, org, env, mine, base, as_halfvec(vector), CANDIDATES_PER_BRANCH
            ),
            self._pool.fetch(_BEST_WORDED, org, env, mine, base, query, CANDIDATES_PER_BRANCH),
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
