"""Knowledge bases: the Markdown cutter, bases and files in Postgres, the search, the golden."""

import hashlib
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import LiteralString

from psycopg import sql
from psycopg.rows import DictRow

from pinecall.domain.agent import KnowledgeFile
from pinecall.domain.errors import WrongModel
from pinecall.domain.names import Env
from pinecall.domain.scope import Scope
from pinecall.postgres.pool import Pool
from pinecall.retrieval._search import (
    CANDIDATES_PER_BRANCH,
    Evidence,
    Figures,
    SearchedTable,
    evidence_of,
    figures,
    hybrid,
    relative_to_the_best,
    top_cosine,
)
from pinecall.retrieval.embed import Embedder, estimated_tokens, halfvec

# Small enough for eight chunks a turn, large enough for a whole tariff table.
CHUNK_TOKENS = 350


# U+203A, the single right-pointing angle quote the stored heading paths are joined with.
HEADING_SEPARATOR = " \u203a "


# Both indexes read the heading path above the body, so a heading's words are searchable.
HEADING_JOINT = "\n\n"


# Only levels 1 to 3 cut a file; a deeper heading is body.
A_HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*$")


# A line inside a code fence is never a heading: `# install` in a shell block is a comment.
A_FENCE = re.compile(r"^\s*(```|~~~)")


A_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")


A_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


# Front matter is metadata: left in, a scraped site's became a chunk per page.
FRONT_MATTER = "---\n"


A_CLOSING_RULE = re.compile(r"^---[ \t]*$", re.MULTILINE)


TEXT_INDEX = "knowledge_chunks_text_bm25"


# '' sorts before any member id, so `holder DESC` puts the holder's own copy first: a read falls
# back to the org's copy, a write never does.
BASES = """
SELECT DISTINCT ON (base) base, chunks, model, extract(epoch FROM pushed_at)::float8 AS pushed_at
FROM knowledge_bases
WHERE org = %(org)s AND env = %(env)s AND holder IN (%(holder)s, '')
ORDER BY base, holder DESC
"""


COPY = """
WITH copy AS (
    SELECT holder FROM knowledge_bases
    WHERE org = %(org)s AND env = %(env)s AND holder IN (%(holder)s, '') AND base = %(base)s
    ORDER BY holder DESC LIMIT 1
)
SELECT file.path, length(file.text) AS chars, file.chunks, file.sha256, file.text,
    extract(epoch FROM file.pushed_at)::float8 AS pushed_at
FROM copy LEFT JOIN knowledge_files AS file
    ON file.org = %(org)s AND file.env = %(env)s AND file.holder = copy.holder
    AND file.base = %(base)s AND file.path = coalesce(%(path)s::text, file.path)
ORDER BY file.path
"""


OWN = """
SELECT base.model, file.path, file.sha256
FROM knowledge_bases AS base LEFT JOIN knowledge_files AS file USING (org, env, holder, base)
WHERE base.org = %(org)s AND base.env = %(env)s AND base.holder = %(holder)s
    AND base.base = %(base)s
"""


# One statement, so a push lands whole or not at all. The subquery reads the rows as they were
# before the statement: the chunks it removes. `keep` null keeps every other file.
WRITE = """
WITH based AS (
    INSERT INTO knowledge_bases (org, env, holder, base, model, dimensions, chunks, pushed_at)
    VALUES (%(org)s, %(env)s, %(holder)s, %(base)s, %(model)s, %(dimensions)s, %(added)s,
        to_timestamp(%(at)s))
    ON CONFLICT (org, env, holder, base) DO UPDATE
    SET model = excluded.model, dimensions = excluded.dimensions, pushed_at = excluded.pushed_at,
        chunks = knowledge_bases.chunks + excluded.chunks - (
            SELECT count(*) FROM knowledge_chunks AS gone
            WHERE gone.org = %(org)s AND gone.env = %(env)s AND gone.holder = %(holder)s
                AND gone.base = %(base)s
                AND (gone.path <> ALL (coalesce(%(keep)s::text[], ARRAY[gone.path]))
                    OR gone.path = ANY (%(changed)s::text[]))
        )
    RETURNING base
), replaced AS (
    DELETE FROM knowledge_chunks
    WHERE org = %(org)s AND env = %(env)s AND holder = %(holder)s AND base = %(base)s
        AND (path <> ALL (coalesce(%(keep)s::text[], ARRAY[path]))
            OR path = ANY (%(changed)s::text[]))
), forgotten AS (
    DELETE FROM knowledge_files
    WHERE org = %(org)s AND env = %(env)s AND holder = %(holder)s AND base = %(base)s
        AND path <> ALL (coalesce(%(keep)s::text[], ARRAY[path]))
), filed AS (
    INSERT INTO knowledge_files (org, env, holder, base, path, text, chunks, sha256, pushed_at)
    SELECT %(org)s, %(env)s, %(holder)s, %(base)s, file.path, file.text, file.chunks,
        file.sha256, to_timestamp(%(at)s)
    FROM unnest(%(changed)s::text[], %(texts)s::text[], %(counts)s::integer[],
        %(hashes)s::text[]) AS file (path, text, chunks, sha256)
    ON CONFLICT (org, env, holder, base, path) DO UPDATE
    SET text = excluded.text, chunks = excluded.chunks, sha256 = excluded.sha256,
        pushed_at = excluded.pushed_at
)
INSERT INTO knowledge_chunks (org, env, holder, base, path, heading, ordinal, text, embedding)
SELECT %(org)s, %(env)s, %(holder)s, %(base)s, chunk.path, chunk.heading, chunk.ordinal,
    chunk.text, chunk.embedding::halfvec
FROM unnest(%(paths)s::text[], %(headings)s::text[], %(ordinals)s::integer[],
    %(indexed)s::text[], %(vectors)s::text[]) AS chunk (path, heading, ordinal, text, embedding)
"""


# Chunks cascade from the base.
DROP = """
DELETE FROM knowledge_bases
WHERE org = %(org)s AND env = %(env)s AND holder = %(holder)s AND base = %(base)s
RETURNING base
"""


# The count reads the chunks as they were before the statement: the ones it removes.
DROP_FILE = """
WITH gone AS (
    DELETE FROM knowledge_files
    WHERE org = %(org)s AND env = %(env)s AND holder = %(holder)s AND base = %(base)s
        AND path = %(path)s
    RETURNING path
), cut AS (
    DELETE FROM knowledge_chunks
    WHERE org = %(org)s AND env = %(env)s AND holder = %(holder)s AND base = %(base)s
        AND path IN (SELECT path FROM gone)
), counted AS (
    UPDATE knowledge_bases SET chunks = chunks - (
        SELECT count(*) FROM knowledge_chunks
        WHERE org = %(org)s AND env = %(env)s AND holder = %(holder)s AND base = %(base)s
            AND path IN (SELECT path FROM gone)
    )
    WHERE org = %(org)s AND env = %(env)s AND holder = %(holder)s AND base = %(base)s
)
SELECT count(*) AS gone FROM gone
"""


# A second statement: a row cannot be updated and deleted in the same one.
DROP_AN_EMPTY_BASE = """
DELETE FROM knowledge_bases AS base
WHERE org = %(org)s AND env = %(env)s AND holder = %(holder)s AND base = %(base)s
    AND NOT EXISTS (
        SELECT 1 FROM knowledge_files AS file
        WHERE file.org = base.org AND file.env = base.env AND file.holder = base.holder
            AND file.base = base.base
    )
"""


# Every holder's rows count: they are the org's, and they fill this world's disk.
KEPT = """
SELECT coalesce(sum(chunks), 0)::integer AS kept FROM knowledge_bases
WHERE org = %(org)s AND env = %(env)s
"""


HELD = """
SELECT count(*)::integer AS held FROM knowledge_chunks
WHERE org = %(org)s AND env = %(env)s AND holder = %(holder)s AND base = %(base)s
    AND path = coalesce(%(path)s::text, path)
"""


# Each base's copy is the holder's own, else the org's: the one its model is checked against.
COPIES = """
SELECT DISTINCT ON (base) base, holder, model FROM knowledge_bases
WHERE org = %(org)s AND env = %(env)s AND holder IN (%(holder)s, '') AND base = ANY (%(bases)s)
ORDER BY base, holder DESC
"""


SCOPE: LiteralString = """
org = %(org)s AND env = %(env)s AND (base, holder) IN (
    SELECT copy.base, copy.holder FROM unnest(%(bases)s::text[], %(holders)s::text[])
        AS copy (base, holder)
)
"""


@dataclass(frozen=True)
class Piece:
    """One chunk of a file before it is embedded: its heading path and its body."""

    path: str
    heading: str | None
    ordinal: int
    text: str


@dataclass(frozen=True)
class Cut:
    """A file as a push writes it: its text, the hash of it, and its pieces."""

    path: str
    text: str
    sha256: str
    pieces: tuple[Piece, ...]


@dataclass(frozen=True)
class Base:
    """One base as listed: its size, the model that wrote its vectors, when it was pushed."""

    base: str
    chunks: int
    model: str
    pushed_at: float


@dataclass(frozen=True)
class File:
    """One file of a base; its text only when the file is read alone."""

    path: str
    chars: int
    chunks: int
    pushed_at: float
    sha256: str
    text: str | None = None


@dataclass(frozen=True)
class Push:
    """What a push writes into a base: the files as cut, and when it was pushed."""

    base: str
    files: tuple[Cut, ...]
    at: float

    @property
    def chunks(self) -> int:
        """How many chunks the files were cut into."""
        return sum(len(item.pieces) for item in self.files)


@dataclass(frozen=True)
class _Own:
    model: str
    hashes: dict[str, str]


@dataclass(frozen=True)
class SearchQuery:
    """What a search asks: the words, how many chunks at most, and each base with its floor."""

    query: str
    k: int
    # A chunk's fused score must reach its own base's floor; 0.0 is none.
    bases: Mapping[str, float]


@dataclass(frozen=True)
class Found:
    """One chunk found: where it is, its body, its score in this search, its cosine."""

    id: str
    base: str
    path: str
    heading: str | None
    text: str
    score: float
    cosine: float


@dataclass(frozen=True)
class Searched:
    """What a search found, and how strongly its best cosine says the answer is there."""

    found: list[Found]
    top_cosine: float
    evidence: Evidence
    model: str


@dataclass(frozen=True)
class Question:
    """A golden question and the heading path that should answer it."""

    asks: str
    expects: str


@dataclass(frozen=True)
class Answered:
    """A question and the chunks the search handed back, best first."""

    question: Question
    found: list[Found]


@dataclass(frozen=True)
class Score:
    """A golden run: how many questions, at which k, its two figures, and the questions missed."""

    questions: int
    k: int
    figures: Figures
    misses: list[Answered]


def chunks_of(path: str, text: str) -> list[Piece]:
    """The file cut at its headings, each piece under the cap, in file order."""
    pieces: list[Piece] = []
    for heading, body in _sections_of(_without_front_matter(text)):
        for group in _under_the_cap(body, heading):
            pieces.append(Piece(path, heading, len(pieces), group))
    return pieces


def cut(file: KnowledgeFile) -> Cut:
    """The file cut once, with the hash a later push compares against."""
    digest = hashlib.sha256(file.text.encode("utf-8")).hexdigest()
    return Cut(file.path, file.text, digest, tuple(chunks_of(file.path, file.text)))


def indexed_text(piece: Piece) -> str:
    """What both indexes read: the heading path above the body, or the body alone."""
    return f"{piece.heading}{HEADING_JOINT}{piece.text}" if piece.heading else piece.text


def body_of(indexed: str, heading: str | None) -> str:
    """The indexed text without its heading path: what the model reads."""
    return indexed.removeprefix(f"{heading}{HEADING_JOINT}") if heading else indexed


async def bases(pool: Pool, scope: Scope) -> list[Base]:
    """The holder's bases, and the org's where the holder has pushed none of that name."""
    async with pool.connection() as connection:
        rows = await (await connection.execute(BASES, _where(scope))).fetchall()
    return [
        Base(base=row["base"], chunks=row["chunks"], model=row["model"], pushed_at=row["pushed_at"])
        for row in rows
    ]


async def files(pool: Pool, scope: Scope, base: str) -> list[File] | None:
    """Every file of the copy the holder reads, by path; None when there is no such base."""
    rows = await _copy(pool, scope, base, None)
    if not rows:
        return None
    return [_file(row, text=None) for row in rows if row["path"] is not None]


async def file(pool: Pool, scope: Scope, base: str, path: str) -> File | None:
    """One file of the copy the holder reads, with its text."""
    rows = await _copy(pool, scope, base, path)
    found = [row for row in rows if row["path"] is not None]
    return _file(found[0], text=found[0]["text"]) if found else None


async def put(pool: Pool, embedder: Embedder, scope: Scope, push: Push) -> None:
    """Replace the holder's base with the push; only the files whose text changed are embedded."""
    await _written(pool, embedder, scope, push, keep=[item.path for item in push.files])


async def put_file(pool: Pool, embedder: Embedder, scope: Scope, push: Push) -> None:
    """Put the push's files beside the base's others, beginning the base when there is none."""
    own = await _own(pool, scope, push.base)
    if own is not None and own.model != embedder.embedding.model:
        raise WrongModel(push.base, own.model, embedder.embedding.model)
    await _written(pool, embedder, scope, push, keep=None)


async def drop(pool: Pool, scope: Scope, base: str) -> bool:
    """Drop the holder's own copy of the base and its chunks; False when it has none."""
    async with pool.connection() as connection:
        dropped = await connection.execute(DROP, {**_where(scope), "base": base})
        return await dropped.fetchone() is not None


async def drop_file(pool: Pool, scope: Scope, base: str, path: str) -> bool:
    """Drop one file of the holder's own copy, and the base with its last file."""
    named = {**_where(scope), "base": base, "path": path}
    async with pool.connection() as connection, connection.transaction():
        row = await (await connection.execute(DROP_FILE, named)).fetchone()
        if row is None or row["gone"] == 0:
            return False
        await connection.execute(DROP_AN_EMPTY_BASE, named)
    return True


async def kept(pool: Pool, org: str, env: Env) -> int:
    """How many chunks the org keeps in the world, every holder's."""
    async with pool.connection() as connection:
        row = await (await connection.execute(KEPT, {"org": org, "env": env})).fetchone()
    return 0 if row is None else row["kept"]


async def chunks_kept(pool: Pool, scope: Scope, base: str, *, path: str | None = None) -> int:
    """How many chunks the holder's own copy of the base holds, or of one file of it."""
    named = {**_where(scope), "base": base, "path": path}
    async with pool.connection() as connection:
        row = await (await connection.execute(HELD, named)).fetchone()
    return 0 if row is None else row["held"]


# Every base in one ranking: fused scores compare only within one, and searched one base at a
# time, an irrelevant base takes a turn's slots with a 1.0 of its own.
async def search(pool: Pool, embedder: Embedder, scope: Scope, query: SearchQuery) -> Searched:
    """The best chunks of every base asked, each over its own floor, at most k."""
    model = embedder.embedding.model
    async with pool.connection() as connection:
        named = {**_where(scope), "bases": list(query.bases)}
        copies = await (await connection.execute(COPIES, named)).fetchall()
    for copy in copies:
        if copy["model"] != model:
            raise WrongModel(copy["base"], copy["model"], model)
    if not copies:
        return Searched(found=[], top_cosine=0.0, evidence="none", model=model)
    [vector] = await embedder.embed([query.query])
    table = SearchedTable(
        name="knowledge_chunks",
        text_index=TEXT_INDEX,
        scope=sql.SQL(SCOPE),
        params={
            "org": scope.org,
            "env": scope.env,
            "bases": [copy["base"] for copy in copies],
            "holders": [copy["holder"] for copy in copies],
        },
        columns=("base", "path", "heading", "text"),
    )
    room = CANDIDATES_PER_BRANCH * len(copies)
    async with pool.connection() as connection:
        hits = await hybrid(connection, table, vector=vector, words=query.query, room=room)
    found = [
        Found(
            id=hit.id,
            base=hit.row["base"],
            path=hit.row["path"],
            heading=hit.row["heading"],
            text=body_of(hit.row["text"], hit.row["heading"]),
            score=hit.fused,
            cosine=hit.cosine,
        )
        for hit in relative_to_the_best(hits)
        if hit.fused >= query.bases[hit.row["base"]]
    ]
    best = top_cosine(hits)
    return Searched(
        found=found[: query.k], top_cosine=best, evidence=evidence_of(best), model=model
    )


def where(found: Found) -> str:
    """The chunk's file, then its heading path."""
    return f"{found.path}{HEADING_SEPARATOR}{found.heading}" if found.heading else found.path


# A file answers for every chunk of it and a heading for its section and what is under it; by
# prefix and separator, never substring: `tarifas.md` is not `tarifas-2024.md`.
def answers(found: str, expects: str) -> bool:
    """Whether a chunk's heading path is the one a golden expects, or under it."""
    wanted = expects.strip()
    return found == wanted or found.startswith(f"{wanted}{HEADING_SEPARATOR}")


def score_docs(answered: Sequence[Answered], k: int) -> Score:
    """Recall at k and nDCG at 10 over every question, and the ones whose chunk never came back."""
    ranks = [_rank(item) for item in answered]
    return Score(
        questions=len(answered),
        k=k,
        figures=figures([[rank] for rank in ranks]),
        misses=[item for item, rank in zip(answered, ranks, strict=True) if rank is None],
    )


# An unclosed `---` is a horizontal rule, and the file is left as it is.
def _without_front_matter(text: str) -> str:
    if not text.startswith(FRONT_MATTER):
        return text
    closed = A_CLOSING_RULE.search(text, len(FRONT_MATTER))
    return text[closed.end() :].lstrip("\n") if closed else text


# A level-n heading closes the open headings of level n and deeper.
def _sections_of(text: str) -> Iterator[tuple[str | None, str]]:
    trail: list[tuple[int, str]] = []
    body: list[str] = []
    fence: str | None = None
    for line in text.split("\n"):
        fenced = A_FENCE.match(line)
        if fenced is not None and fence in {None, fenced.group(1)}:
            fence = None if fence else fenced.group(1)
        heading = None if fence or fenced else A_HEADING.match(line)
        if heading is None:
            body.append(line)
            continue
        yield _path_of(trail), "\n".join(body)
        body = []
        level = len(heading.group(1))
        trail = [*(kept for kept in trail if kept[0] < level), (level, heading.group(2))]
    yield _path_of(trail), "\n".join(body)


def _path_of(trail: Sequence[tuple[int, str]]) -> str | None:
    return HEADING_SEPARATOR.join(title for _, title in trail) or None


# The heading's tokens count against the cap: it is indexed with every piece under it.
def _under_the_cap(body: str, heading: str | None) -> list[str]:
    room = CHUNK_TOKENS - estimated_tokens(heading or "")
    paragraphs = [paragraph.strip() for paragraph in A_PARAGRAPH_BREAK.split(body)]
    parts = [part for paragraph in paragraphs if paragraph for part in _within(paragraph, room)]
    return _grouped(parts, HEADING_JOINT, room)


def _within(paragraph: str, room: int) -> list[str]:
    if estimated_tokens(paragraph) <= room:
        return [paragraph]
    return _grouped(A_SENTENCE_END.split(paragraph), " ", room)


# A part longer than the room is kept whole rather than cut mid-sentence.
def _grouped(parts: Sequence[str], joint: str, room: int) -> list[str]:
    groups: list[str] = []
    current: list[str] = []
    for part in parts:
        if current and estimated_tokens(joint.join([*current, part])) > room:
            groups.append(joint.join(current))
            current = []
        current.append(part)
    if current:
        groups.append(joint.join(current))
    return groups


async def _copy(pool: Pool, scope: Scope, base: str, path: str | None) -> list[DictRow]:
    named = {**_where(scope), "base": base, "path": path}
    async with pool.connection() as connection:
        return await (await connection.execute(COPY, named)).fetchall()


async def _own(pool: Pool, scope: Scope, base: str) -> _Own | None:
    async with pool.connection() as connection:
        rows = await (await connection.execute(OWN, {**_where(scope), "base": base})).fetchall()
    if not rows:
        return None
    hashes = {row["path"]: row["sha256"] for row in rows if row["path"] is not None}
    return _Own(model=rows[0]["model"], hashes=hashes)


# A hash is only worth its vectors under the model that wrote them: under another, every file is
# embedded again. The vendor answers before the statement runs, so no connection waits on it.
async def _written(
    pool: Pool, embedder: Embedder, scope: Scope, push: Push, *, keep: list[str] | None
) -> None:
    own = await _own(pool, scope, push.base)
    same_space = own is not None and own.model == embedder.embedding.model
    known = own.hashes if own is not None and same_space else {}
    changed = [item for item in push.files if known.get(item.path) != item.sha256]
    documents = [[indexed_text(piece) for piece in item.pieces] for item in changed if item.pieces]
    embedded = await embedder.embed_documents(documents) if documents else []
    pieces = [piece for item in changed for piece in item.pieces]
    written = [vector for document in embedded for vector in document]
    named = {
        **_where(scope),
        "base": push.base,
        "model": embedder.embedding.model,
        "dimensions": embedder.embedding.dimensions,
        "added": len(pieces),
        "at": push.at,
        "keep": keep,
        "changed": [item.path for item in changed],
        "texts": [item.text for item in changed],
        "counts": [len(item.pieces) for item in changed],
        "hashes": [item.sha256 for item in changed],
        "paths": [piece.path for piece in pieces],
        "headings": [piece.heading for piece in pieces],
        "ordinals": [piece.ordinal for piece in pieces],
        "indexed": [indexed_text(piece) for piece in pieces],
        "vectors": [halfvec(vector) for vector in written],
    }
    async with pool.connection() as connection:
        await connection.execute(WRITE, named)


def _file(row: DictRow, *, text: str | None) -> File:
    return File(
        path=row["path"],
        chars=row["chars"],
        chunks=row["chunks"],
        pushed_at=row["pushed_at"],
        sha256=row["sha256"],
        text=text,
    )


def _where(scope: Scope) -> dict[str, object]:
    return {"org": scope.org, "env": scope.env, "holder": scope.holder}


# Only the first match counts: a base that repeats itself answered once.
def _rank(item: Answered) -> int | None:
    return next(
        (
            at
            for at, chunk in enumerate(item.found, start=1)
            if answers(where(chunk), item.question.expects)
        ),
        None,
    )
