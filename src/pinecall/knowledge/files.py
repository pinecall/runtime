"""The files of a base, one at a time: listed, read whole, put and re-cut alone, taken out."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pinecall.knowledge.chunking import chunks_of
from pinecall.log.store import Pool
from pinecall.providers.embedder import Embedder, WrongModel, as_halfvec
from pinecall.types import Env, KnowledgeFile, whose

# The push's sentence, said of one file: a file put into a base another model wrote would be
# vectors of two models in one index, and a search over that is a number with no meaning.
PUSHED_WITH_ANOTHER_MODEL = (
    "base {base} was pushed with {pushed}; this gateway embeds with {mine}: push it again"
)


@dataclass(frozen=True)
class File:
    """One file of a base as a listing draws it: its path, its size, what it became, and when."""

    path: str
    chars: int
    chunks: int
    pushed_at: datetime
    # The whole text, when one file was asked for; a listing carries the size and not the text.
    text: str | None = None


# Whose copy of the base a READ is about: yours when you pushed one, the org's otherwise — the
# same rule `store.py` reads chunks by, written the same way so the two cannot drift.
_WHOSE = """
    holder = (SELECT holder FROM knowledge_bases
              WHERE org = $1 AND env = $2 AND holder IN ($3, '') AND base = $4
              ORDER BY holder DESC LIMIT 1)
"""

_FILES = f"""
SELECT path, length(text) AS chars, chunks, pushed_at
FROM knowledge_files
WHERE org = $1 AND env = $2 AND base = $4 AND {_WHOSE}
ORDER BY path
"""

_FILE = f"""
SELECT path, length(text) AS chars, chunks, pushed_at, text
FROM knowledge_files
WHERE org = $1 AND env = $2 AND base = $4 AND path = $5 AND {_WHOSE}
"""

# A WRITE is about this corner's own copy and never the org's, as a push is: what this corner
# already holds under that path is what the put frees, and nothing when it holds none.
_OWN_BASE = """
SELECT model, chunks FROM knowledge_bases
WHERE org = $1 AND env = $2 AND holder = $3 AND base = $4
"""

_OWN_FILE_CHUNKS = """
SELECT chunks FROM knowledge_files
WHERE org = $1 AND env = $2 AND holder = $3 AND base = $4 AND path = $5
"""

# One statement, so a put is all or nothing: the base's row made or bumped — its count moved by
# what this file freed and what it became, both read off the snapshot the statement opened on —
# the file's old chunks gone, its new ones in, and the file itself kept or replaced.
_PUT_FILE = """
WITH pushed AS (
    INSERT INTO knowledge_bases (org, env, holder, base, model, dimensions, chunks, pushed_at)
        VALUES ($1, $2, $3, $4, $5, $6, $8, now())
        ON CONFLICT (org, env, holder, base) DO UPDATE
        SET chunks = knowledge_bases.chunks
                     - coalesce((SELECT f.chunks FROM knowledge_files f
                                 WHERE f.org = $1 AND f.env = $2 AND f.holder = $3
                                   AND f.base = $4 AND f.path = $7), 0)
                     + $8,
            pushed_at = now()
), replaced AS (
    DELETE FROM knowledge_chunks
    WHERE org = $1 AND env = $2 AND holder = $3 AND base = $4 AND path = $7
), filed AS (
    INSERT INTO knowledge_files (org, env, holder, base, path, text, chunks, pushed_at)
        VALUES ($1, $2, $3, $4, $7, $9, $8, now())
        ON CONFLICT (org, env, holder, base, path) DO UPDATE
        SET text = excluded.text, chunks = excluded.chunks, pushed_at = now()
)
INSERT INTO knowledge_chunks (org, env, holder, base, path, heading, ordinal, text, embedding)
SELECT $1, $2, $3, $4, $7, chunk.heading, chunk.ordinal, chunk.text, chunk.embedding::halfvec
FROM unnest($10::text[], $11::integer[], $12::text[], $13::text[])
    AS chunk (heading, ordinal, text, embedding)
"""

# The file and its chunks gone and the base's count moved, in one statement. The row returned
# answers "was there one", so taking out a path nobody put is told apart from taking out a file.
_DROP_FILE = """
WITH gone AS (
    DELETE FROM knowledge_files
    WHERE org = $1 AND env = $2 AND holder = $3 AND base = $4 AND path = $5
    RETURNING chunks
), cut AS (
    DELETE FROM knowledge_chunks
    WHERE org = $1 AND env = $2 AND holder = $3 AND base = $4 AND path = $5
), counted AS (
    UPDATE knowledge_bases
    SET chunks = chunks - (SELECT coalesce(sum(chunks), 0) FROM gone)
    WHERE org = $1 AND env = $2 AND holder = $3 AND base = $4
      AND EXISTS (SELECT 1 FROM gone)
)
SELECT chunks FROM gone
"""

# A base with no file left is no base: its row would list as a name with nothing in it. Its own
# statement, after the file is gone — one row cannot be updated and deleted in the same one.
_DROP_AN_EMPTY_BASE = """
DELETE FROM knowledge_bases
WHERE org = $1 AND env = $2 AND holder = $3 AND base = $4
  AND NOT EXISTS (SELECT 1 FROM knowledge_files
                  WHERE org = $1 AND env = $2 AND holder = $3 AND base = $4)
"""


async def files(pool: Pool, org: str, env: Env, holder: str | None, base: str) -> list[File]:
    """Every file of the base this corner reads, by path, without their text."""
    rows = await pool.fetch(_FILES, org, env, whose(holder), base)
    return [_a_file(row) for row in rows]


async def file(
    pool: Pool, org: str, env: Env, holder: str | None, base: str, path: str
) -> File | None:
    """One file of the base this corner reads, text and all; None when there is no such file."""
    row = await pool.fetchrow(_FILE, org, env, whose(holder), base, path)
    return None if row is None else _a_file(row, text=str(row["text"]))


async def freed_by(pool: Pool, org: str, env: Env, holder: str | None, base: str, path: str) -> int:
    """How many chunks THIS corner's copy of the file holds now: what a put of it would free."""
    row = await pool.fetchrow(_OWN_FILE_CHUNKS, org, env, whose(holder), base, path)
    return 0 if row is None else int(row["chunks"])


async def put_file(
    pool: Pool,
    embedder: Embedder,
    org: str,
    env: Env,
    holder: str | None,
    base: str,
    file: KnowledgeFile,
) -> int:
    """This corner's copy of the file replaced — or the base begun with it; how many chunks."""
    mine = await embedder.model()
    own = await pool.fetchrow(_OWN_BASE, org, env, whose(holder), base)
    if own is not None and str(own["model"]) != mine:
        raise WrongModel(
            PUSHED_WITH_ANOTHER_MODEL.format(base=base, pushed=str(own["model"]), mine=mine)
        )
    pieces = chunks_of(file)
    [vectors] = await embedder.embed_documents([[piece.text for piece in pieces]])
    await pool.execute(
        _PUT_FILE,
        org,
        env,
        whose(holder),
        base,
        mine,
        embedder.dimensions,
        file.path,
        len(pieces),
        file.text,
        [piece.heading for piece in pieces],
        [piece.ordinal for piece in pieces],
        [piece.text for piece in pieces],
        [as_halfvec(vector) for vector in vectors],
    )
    return len(pieces)


async def drop_file(
    pool: Pool, org: str, env: Env, holder: str | None, base: str, path: str
) -> bool:
    """This corner's copy of the file and its chunks gone; the base too when it was the last."""
    gone = await pool.fetchrow(_DROP_FILE, org, env, whose(holder), base, path)
    if gone is None:
        return False
    await pool.execute(_DROP_AN_EMPTY_BASE, org, env, whose(holder), base)
    return True


def as_columns(files: Sequence[KnowledgeFile]) -> tuple[list[str], list[str], list[int]]:
    """The files as a push's three arrays: the paths, the texts, and how many chunks each became."""
    return (
        [file.path for file in files],
        [file.text for file in files],
        [len(chunks_of(file)) for file in files],
    )


def _a_file(row: Mapping[str, Any], text: str | None = None) -> File:
    return File(
        path=str(row["path"]),
        chars=int(row["chars"]),
        chunks=int(row["chunks"]),
        pushed_at=row["pushed_at"],
        text=text,
    )
