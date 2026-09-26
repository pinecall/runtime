"""The knowledge base in Postgres: a push replaces a base whole, a search reads it two ways."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pinecall.knowledge import files as the_files
from pinecall.knowledge.chunking import chunks_of
from pinecall.knowledge.files import PUSHED_WITH_ANOTHER_MODEL, File
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

# What a caller hears for the one mistake this signature invites.
ONE_BASE_IS_STILL_A_LIST = (
    "search takes the bases a turn reads, as a list: [{base!r}], not {base!r}"
)

# Whose copy of each base this corner reads, and what wrote its vectors: yours where you pushed
# one, the org's otherwise — '' sorts before any member id, so DESC puts yours first. One
# definition read twice: the model a base was pushed with, and the holder the two branches join
# their chunks to. A base the org never pushed is simply absent, which is not an error here: a
# search of a name nobody pushed answers with nothing, as it always did.
_MINE = """
SELECT DISTINCT ON (base) base, holder, model FROM knowledge_bases
WHERE org = $1 AND env = $2 AND holder IN ($3, '') AND base = ANY($4::text[])
ORDER BY base, holder DESC
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

# Both branches read EVERY base the turn asks for, in one pass, and that is the whole of the
# multi-base design: a score is only comparable to the scores it was ranked against. Searched one
# base at a time and merged afterwards, each base's own best came back at 1.0 — the fusion reads
# relative to the best of ITS query — so three attached collections took three of a turn's four
# slots before the ranking said anything, whatever the third was about (measured 2026-09-20).
#
# Both branches read `mode = 'retrieved'`, the filter 0038 promised when it let a row be kept
# whole with no vector: nothing writes such a row yet, and a search must never rank one.
# The dense branch: nearest by cosine, the HNSW index's own order. The corner is JOINED and not
# asked per candidate: as a correlated subquery it ran once per row — 537 times over a tenant's
# base, 19ms — and as a join the four columns of `knowledge_chunks_by_base` are one index
# condition, 5ms (measured on the box, 2026-09-20).
_NEAREST = f"""
WITH mine AS ({_MINE})
SELECT id, base, path, heading, text
FROM knowledge_chunks JOIN mine USING (base, holder)
WHERE org = $1 AND env = $2 AND mode = 'retrieved'
ORDER BY embedding <=> $5::halfvec
LIMIT $6
"""

# The words branch. `<@>` answers the negative BM25 score, lower is better, and 0 is a text
# none of the query's terms is in — which is not a candidate, so it never earns a rank.
_BEST_WORDED = f"""
WITH mine AS ({_MINE})
SELECT id, base, path, heading, text
FROM (
    SELECT id, base, path, heading, text, text <@> to_bm25query($5, '{TEXT_INDEX}') AS score
    FROM knowledge_chunks JOIN mine USING (base, holder)
    WHERE org = $1 AND env = $2 AND mode = 'retrieved'
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
        bases: Sequence[str],
        query: str,
        *,
        k: int = DEFAULT_CHUNKS_PER_TURN,
        floors: Mapping[str, float | None] | None = None,
    ) -> list[Chunk]:
        """The best k chunks of every base asked, by meaning and by words, fused once together."""
        # A `str` IS a Sequence[str], so a caller that hands one base the old way asks for its
        # LETTERS and is answered nothing at all, silently — twelve tests read an empty set before
        # anybody read this line. The refusal is a sentence because a type checker cannot say it.
        if isinstance(bases, str):
            raise TypeError(ONE_BASE_IS_STILL_A_LIST.format(base=bases))
        if not bases:
            return []
        [vector] = await self._embedder.embed([query])
        # The base's row rides along with the two branches instead of gating them: it is one more
        # index read on a primary key, and the turn's budget covers the slowest of the three
        # rather than their sum. The refusal comes before a rank is read, either way.
        mine = whose(holder)
        # The candidate pool grows with the bases asked, so a small collection is not crowded out
        # of the ranking by a big one before the fusion has read either of them.
        room = CANDIDATES_PER_BRANCH * len(bases)
        asked = list(bases)
        pushed, nearest, worded = await asyncio.gather(
            self._pool.fetch(_MINE, org, env, mine, asked),
            self._pool.fetch(_NEAREST, org, env, mine, asked, as_halfvec(vector), room),
            self._pool.fetch(_BEST_WORDED, org, env, mine, asked, query, room),
        )
        _every_base_on_the_same_model(pushed, await self._embedder.model())
        # One fusion over the union, so the scores are comparable; then each chunk against the
        # floor of ITS OWN base, because a threshold is the attachment's and not the turn's.
        chunks = [_a_chunk(row, score) for row, score in _fused((nearest, worded))]
        return [chunk for chunk in chunks if _above_its_floor(chunk, floors)][:k]


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


def _every_base_on_the_same_model(pushed: Sequence[Mapping[str, Any]], mine: str) -> None:
    """Refuse the first base whose vectors another model wrote, naming both and the way out."""
    for row in pushed:
        if str(row["model"]) != mine:
            raise WrongModel(
                PUSHED_WITH_ANOTHER_MODEL.format(
                    base=str(row["base"]), pushed=str(row["model"]), mine=mine
                )
            )


# A threshold belongs to the attachment that set it — `docs attach <base> --min-score` — so it is
# read against the chunk's own base and never against the turn's other collections.
def _above_its_floor(chunk: Chunk, floors: Mapping[str, float | None] | None) -> bool:
    """Whether this chunk clears the floor the world put on the base it came from."""
    floor = None if floors is None else floors.get(chunk.base)
    return floor is None or chunk.score >= floor


def _a_chunk(row: Mapping[str, Any], score: float) -> Chunk:
    """One row back into the shape retrieval hands out; the row says which base it is from."""
    return Chunk(
        id=str(row["id"]),
        base=str(row["base"]),
        path=str(row["path"]),
        heading=None if row["heading"] is None else str(row["heading"]),
        text=str(row["text"]),
        score=score,
    )
