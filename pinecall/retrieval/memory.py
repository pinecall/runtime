"""Contact memory: the facts table, recalled per turn, held and forgotten, and its goldens."""

import dataclasses
import logging
import time
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from psycopg import sql
from psycopg.rows import DictRow
from pydantic import BaseModel, ConfigDict, Field

from pinecall.domain.errors import DeclarationRefused
from pinecall.domain.names import Env
from pinecall.domain.scope import Scope
from pinecall.postgres.pool import Connection, Pool
from pinecall.retrieval._search import (
    CANDIDATES_PER_BRANCH,
    Evidence,
    Figures,
    Hit,
    SearchedTable,
    evidence_of,
    figures,
    hybrid,
    relative_to_the_best,
    top_cosine,
)
from pinecall.retrieval.embed import Embedder, halfvec
from pinecall.wire.rest.retrieval import (
    MemoryGolden,
    MemoryMiss,
    MemoryQuestion,
    MemoryScore,
)

logger = logging.getLogger(__name__)


DEFAULT_FACTS_PER_TURN = 6


# A fact learned a half-life ago weighs half of one learned now.
HALF_LIFE_DAYS = 90


A_DAY_S = 86_400


TEXT_INDEX = "contact_memories_text_bm25"


COLUMNS = (
    "id",
    "contact",
    "category",
    "text",
    "valid_from",
    "invalidated_at",
    "supersedes",
    "source_call",
    "model",
    "confidence",
)


# There is no delete: a fact is only ever superseded or ended, and the history keeps it.
type OpName = Literal["add", "update", "invalidate"]


BAD_CURSOR = "{after!r} is not a cursor: pass the `next` a page answered, or nothing"


HELD_NOW = sql.SQL(
    "org = %(org)s AND env = %(env)s AND holder = %(holder)s AND contact = %(contact)s"
    " AND invalidated_at IS NULL"
)


HELD_THEN = sql.SQL(
    "org = %(org)s AND env = %(env)s AND holder = %(holder)s AND contact = %(contact)s"
    " AND valid_from <= %(as_of)s AND (invalidated_at IS NULL OR invalidated_at > %(as_of)s)"
)


# Vectors of two models share a width and still mean nothing to each other.
SAME_SPACE = sql.SQL("model = %(model)s")


ENDED = """
UPDATE contact_memories SET invalidated_at = %(at)s
WHERE id = %(of)s::uuid AND org = %(org)s AND env = %(env)s AND holder = %(holder)s
  AND invalidated_at IS NULL
RETURNING id
"""


# The count comes back as a row, not read off the command tag.
FORGOTTEN = """
WITH gone AS (
    DELETE FROM contact_memories
    WHERE org = %(org)s AND env = %(env)s AND holder = %(holder)s AND contact = %(contact)s
    RETURNING id
)
SELECT count(*) AS forgotten FROM gone
"""


# What the quota counts: this world's current facts, every holder's; history is not a fact held.
KEPT = """
SELECT count(*) AS kept FROM contact_memories
WHERE org = %(org)s AND env = %(env)s AND invalidated_at IS NULL
"""


STALE = """
SELECT id, text FROM contact_memories WHERE model <> %(model)s ORDER BY created_at, id
"""


REEMBEDDED = """
UPDATE contact_memories SET embedding = %(embedding)s::halfvec, model = %(model)s
WHERE id = %(id)s AND model <> %(model)s
"""


@dataclass(frozen=True)
class Fact:
    """One row of a contact's memory: what it says, when it held, and what wrote it."""

    id: str
    contact: str
    category: str | None
    text: str
    valid_from: datetime
    invalidated_at: datetime | None = None
    supersedes: str | None = None
    # The call that taught it; None for a golden's.
    taught_by: str | None = None
    model: str = ""
    confidence: float = 1.0


@dataclass(frozen=True)
class RecalledFact:
    """A fact recalled, and its score relative to the best of the recall."""

    fact: Fact
    score: float


@dataclass(frozen=True)
class Recalled:
    """What a recall found, best first, and how strongly the best vector says it answers."""

    facts: list[RecalledFact]
    top_cosine: float
    evidence: Evidence


@dataclass(frozen=True)
class Recall:
    """A recall of one contact's facts: the query, the moment, how many, and what held when."""

    contact: str
    query: str
    at: datetime
    k: int = DEFAULT_FACTS_PER_TURN
    # Reads the facts that held at that moment rather than the current ones.
    as_of: datetime | None = None


@dataclass(frozen=True)
class Taught:
    """When a contact's memory was taught, and on which call: what every written fact carries."""

    contact: str
    call: str | None
    at: datetime


@dataclass(frozen=True)
class Held:
    """Sentences written as a contact's facts with no model, all at one moment."""

    contact: str
    sentences: tuple[str, ...]
    at: datetime


@dataclass(frozen=True)
class Op:
    """One change the extraction model asked of a contact's memory."""

    op: OpName
    text: str = ""
    category: str | None = None
    of: str | None = None


class Paging(BaseModel):
    """A page of current facts: after which cursor, the words to look for, and how many."""

    model_config = ConfigDict(frozen=True)

    after: str | None = None
    q: str | None = Field(default=None, max_length=200)
    limit: int = Field(default=50, ge=1, le=200)


@dataclass(frozen=True)
class FactsPage:
    """A page of facts, newest first, the cursor of the next, and the agent that taught each."""

    facts: list[Fact]
    next: str | None
    agents: Mapping[str, str] = field(default_factory=dict[str, str])


@dataclass(frozen=True)
class Answered:
    """A golden's question, and the texts of the facts recalled for it, best first."""

    question: MemoryQuestion
    found: tuple[str, ...]

    # Only the first fact that says it counts: memory repeating itself answered once.
    @property
    def ranks(self) -> tuple[int | None, ...]:
        """The rank of each expected fact, from 1, or None when it was not recalled."""
        return tuple(
            next((at for at, text in enumerate(self.found, 1) if says(text, expected)), None)
            for expected in self.question.expects
        )

    @property
    def missing(self) -> tuple[str, ...]:
        """The expected facts that were not recalled."""
        return tuple(
            expected
            for expected, rank in zip(self.question.expects, self.ranks, strict=True)
            if rank is None
        )


@dataclass(frozen=True)
class Score:
    """A memory golden's figures over every question, and the questions it missed."""

    questions: int
    k: int
    figures: Figures
    misses: tuple[Answered, ...]


OP_NAMES: tuple[OpName, ...] = ("add", "update", "invalidate")


WRITES: frozenset[OpName] = frozenset({"add", "update"})


NAMES_A_FACT: frozenset[OpName] = frozenset({"update", "invalidate"})


_LISTED = sql.SQL(", ").join(sql.Identifier(column) for column in COLUMNS)


CURRENT = sql.SQL(
    "SELECT {columns} FROM contact_memories WHERE org = %(org)s AND env = %(env)s"
    " AND holder = %(holder)s AND contact = %(contact)s AND invalidated_at IS NULL"
    " ORDER BY valid_from, id"
).format(columns=_LISTED)


HISTORY = sql.SQL(
    "SELECT {columns} FROM contact_memories WHERE org = %(org)s AND env = %(env)s"
    " AND holder = %(holder)s AND contact = %(contact)s"
    " ORDER BY (invalidated_at IS NULL) DESC, valid_from DESC, id"
).format(columns=_LISTED)


ADDED = sql.SQL(
    "INSERT INTO contact_memories"
    " (org, env, holder, contact, text, category, embedding, valid_from, source_call, model)"
    " VALUES (%(org)s, %(env)s, %(holder)s, %(contact)s, %(text)s, %(category)s,"
    " %(embedding)s::halfvec, %(at)s, %(call)s, %(model)s)"
    " RETURNING {columns}"
).format(columns=_LISTED)


# The old row ends and its replacement is written in one statement; nothing is written when the
# old row had already ended.
UPDATED = sql.SQL(
    "WITH ended AS ("
    " UPDATE contact_memories SET invalidated_at = %(at)s"
    " WHERE id = %(of)s::uuid AND org = %(org)s AND env = %(env)s AND holder = %(holder)s"
    " AND contact = %(contact)s AND invalidated_at IS NULL RETURNING id)"
    " INSERT INTO contact_memories"
    " (org, env, holder, contact, text, category, embedding, valid_from, source_call, model,"
    " supersedes)"
    " SELECT %(org)s, %(env)s, %(holder)s, %(contact)s, %(text)s, %(category)s,"
    " %(embedding)s::halfvec, %(at)s, %(call)s, %(model)s, ended.id FROM ended"
    " RETURNING {columns}"
).format(columns=_LISTED)


# A fact is an agent's through the call that taught it: a golden's fact has no call, and no agent.
TAUGHT_BY = sql.SQL(
    "SELECT {columns}, head.agent AS taught_by_agent FROM contact_memories AS memory"
    " JOIN call_log_head AS head ON head.log = memory.source_call"
    " WHERE memory.org = %(org)s AND memory.env = %(env)s AND memory.holder = %(holder)s"
    " AND (%(agent)s::text IS NULL OR head.agent = %(agent)s)"
    " AND memory.invalidated_at IS NULL"
    " AND (%(q)s::text IS NULL OR memory.text ILIKE %(q)s OR memory.contact ILIKE %(q)s"
    " OR memory.category ILIKE %(q)s)"
    " AND (%(after_at)s::timestamptz IS NULL"
    " OR (memory.valid_from, memory.id) < (%(after_at)s::timestamptz, %(after_id)s::uuid))"
    " ORDER BY memory.valid_from DESC, memory.id DESC"
    " LIMIT %(limit)s"
).format(columns=sql.SQL(", ").join(sql.Identifier("memory", column) for column in COLUMNS))


# Memory has no fallback to the org's own facts: a corner recalls its own or nothing.
async def recall(pool: Pool, embedder: Embedder, scope: Scope, request: Recall) -> Recalled:
    """The contact's best facts for the query: both branches fused, then recency and confidence."""
    (vector,) = await embedder.embed([request.query])
    model = embedder.embedding.model
    table = SearchedTable(
        name="contact_memories",
        text_index=TEXT_INDEX,
        scope=HELD_NOW if request.as_of is None else HELD_THEN,
        params={**_where(scope, request.contact), "as_of": request.as_of, "model": model},
        columns=COLUMNS,
        same_space=SAME_SPACE,
    )
    async with pool.connection() as connection:
        hits = await hybrid(
            connection, table, vector=vector, words=request.query, room=CANDIDATES_PER_BRANCH
        )
    # A cosine against another model's vector measures nothing.
    best = top_cosine([hit for hit in hits if hit.row["model"] == model])
    return Recalled(
        facts=ranked(hits, now=request.as_of or request.at, k=request.k),
        top_cosine=best,
        evidence=evidence_of(best),
    )


def ranked(hits: Sequence[Hit], *, now: datetime, k: int) -> list[RecalledFact]:
    """The k best hits once each is weighed by recency and confidence, relative to the best."""
    weighed = [
        dataclasses.replace(
            hit,
            fused=hit.fused * recency(hit.row["valid_from"], now) * hit.row["confidence"],
        )
        for hit in hits
    ]
    return [
        RecalledFact(fact=_fact_of(hit.row), score=hit.fused)
        for hit in relative_to_the_best(weighed)[:k]
    ]


def recency(learned: datetime, now: datetime) -> float:
    """1.0 for a fact learned now, halving every half-life; a date after now counts as now."""
    days = max((now - learned).total_seconds(), 0.0) / A_DAY_S
    return 0.5 ** (days / HALF_LIFE_DAYS)


async def current(pool: Pool, scope: Scope, contact: str) -> list[Fact]:
    """The contact's facts that hold now, oldest first."""
    return await _facts(pool, CURRENT, _where(scope, contact))


async def history(pool: Pool, scope: Scope, contact: str) -> list[Fact]:
    """Every fact ever kept of the contact in the corner, current first, newest first."""
    return await _facts(pool, HISTORY, _where(scope, contact))


async def taught_by(pool: Pool, scope: Scope, agent: str | None, *, page: Paging) -> FactsPage:
    """A page of the current facts the agent's calls taught, newest first; None for every agent."""
    after = _cursor(page.after)
    params: dict[str, object] = {
        "org": scope.org,
        "env": scope.env,
        "holder": scope.holder,
        "agent": agent,
        "q": f"%{_like_escaped(page.q)}%" if page.q else None,
        "after_at": None if after is None else after[0],
        "after_id": None if after is None else after[1],
        "limit": page.limit + 1,
    }
    async with pool.connection() as connection:
        rows = await (await connection.execute(TAUGHT_BY, params)).fetchall()
    shown = rows[: page.limit]
    facts = [_fact_of(row) for row in shown]
    more = _cursor_of(facts[-1]) if len(rows) > page.limit else None
    agents = {fact.id: str(row["taught_by_agent"]) for fact, row in zip(facts, shown, strict=True)}
    return FactsPage(facts=facts, next=more, agents=agents)


async def kept(pool: Pool, org: str, env: Env) -> int:
    """How many current facts the org keeps in the world, across every holder and contact."""
    async with pool.connection() as connection:
        row = await (await connection.execute(KEPT, {"org": org, "env": env})).fetchone()
    return 0 if row is None else int(row["kept"])


async def hold(pool: Pool, embedder: Embedder, scope: Scope, request: Held) -> list[Fact]:
    """The sentences written as the contact's facts, embedded, with no model asked."""
    vectors = await embedder.embed(list(request.sentences)) if request.sentences else []
    base = {**_where(scope, request.contact), "at": request.at, "call": None, "category": None}
    written: list[Fact] = []
    async with pool.connection() as connection, connection.transaction():
        for text, vector in zip(request.sentences, vectors, strict=True):
            fact = await _added(connection, embedder, {**base, "text": text, "of": None}, vector)
            if fact is not None:
                written.append(fact)
    return written


# Every row, superseded ones included: an erasure that left the history would erase nothing.
async def forget(pool: Pool, scope: Scope, contact: str) -> int:
    """Delete every fact of the contact in the corner; how many went, zero included."""
    async with pool.connection() as connection:
        row = await (await connection.execute(FORGOTTEN, _where(scope, contact))).fetchone()
    return 0 if row is None else int(row["forgotten"])


async def invalidated(pool: Pool, scope: Scope, fact_id: str, *, at: datetime) -> bool:
    """End one current fact of the corner at `at`; False when there is none by that id."""
    params = {"org": scope.org, "env": scope.env, "holder": scope.holder, "of": fact_id}
    async with pool.connection() as connection:
        ended = await (await connection.execute(ENDED, {**params, "at": at})).fetchone()
    return ended is not None


# One transaction per batch: a failure leaves the batches before it written, and a second run
# skips them.
async def reembed(pool: Pool, embedder: Embedder, *, batch: int = 64) -> int:
    """Re-embed every fact another model wrote, in every org and world; how many there were."""
    model = embedder.embedding.model
    async with pool.connection() as connection:
        stale = await (await connection.execute(STALE, {"model": model})).fetchall()
    for start in range(0, len(stale), batch):
        rows = stale[start : start + batch]
        vectors = await embedder.embed([str(row["text"]) for row in rows])
        written = [
            {"id": row["id"], "embedding": halfvec(vector), "model": model}
            for row, vector in zip(rows, vectors, strict=True)
        ]
        async with (
            pool.connection() as connection,
            connection.transaction(),
            connection.cursor() as cursor,
        ):
            await cursor.executemany(REEMBEDDED, written)
    return len(stale)


async def ask_golden(
    pool: Pool, embedder: Embedder, scope: Scope, golden: MemoryGolden, *, at: datetime
) -> MemoryScore:
    """Each question's facts written to a scratch contact, recalled, scored, and forgotten."""
    k = golden.k or DEFAULT_FACTS_PER_TURN
    scratch = f"golden-{uuid4().hex}"
    started = time.perf_counter()
    answered: list[Answered] = []
    try:
        for question in golden.questions:
            await hold(pool, embedder, scope, Held(scratch, tuple(question.holds), at))
            recalled = await recall(
                pool, embedder, scope, Recall(scratch, question.asks, at=at, k=k)
            )
            await forget(pool, scope, scratch)
            found = tuple(item.fact.text for item in recalled.facts)
            answered.append(Answered(question=question, found=found))
    finally:
        await forget(pool, scope, scratch)
    score = score_memory(answered, k)
    return MemoryScore(
        model=embedder.embedding.model,
        questions=score.questions,
        k=score.k,
        recall_at_k=score.figures.recall_at_k,
        ndcg_at_10=score.figures.ndcg_at_10,
        took_ms=(time.perf_counter() - started) * 1000,
        misses=[
            MemoryMiss(asks=item.question.asks, missing=list(item.missing), found=list(item.found))
            for item in score.misses
        ],
    )


def score_memory(answered: Sequence[Answered], k: int) -> Score:
    """recall@k and nDCG@10 over every question; a question missing any expected fact is a miss."""
    return Score(
        questions=len(answered),
        k=k,
        figures=figures([item.ranks for item in answered]),
        misses=tuple(item for item in answered if None in item.ranks),
    )


# Facts are written by a model, so the golden is held to containment, one direction: a fact that
# says less than expected is a miss.
def says(found: str, expected: str) -> bool:
    """Whether a recalled fact contains the expected text, accents, case and spacing aside."""
    return _unaccented(expected) in _unaccented(found)


# Embedded before the transaction opens: an update never ends a fact without its replacement.
async def apply_ops(
    pool: Pool, embedder: Embedder, scope: Scope, taught: Taught, ops: Sequence[Op]
) -> list[Fact]:
    """Write the ops as one: adds and updates embedded first; an update ends the row it replaces."""
    writing = [op for op in ops if op.op in WRITES]
    vectors = await embedder.embed([op.text for op in writing]) if writing else []
    vector_of = dict(zip(writing, vectors, strict=True))
    base = {**_where(scope, taught.contact), "at": taught.at, "call": taught.call}
    written: list[Fact] = []
    async with pool.connection() as connection, connection.transaction():
        for op in ops:
            if op.op == "invalidate":
                await connection.execute(ENDED, {**base, "of": op.of})
                continue
            row = {**base, "text": op.text, "category": op.category, "of": op.of}
            fact = await _added(connection, embedder, row, vector_of[op])
            if fact is not None:
                written.append(fact)
    return written


def folded(text: str) -> str:
    """The text case-folded with its spaces squeezed: how two sentences are compared."""
    return " ".join(text.casefold().split())


def _where(scope: Scope, contact: str) -> dict[str, object]:
    return {"org": scope.org, "env": scope.env, "holder": scope.holder, "contact": contact}


async def _facts(pool: Pool, query: sql.Composed, params: Mapping[str, object]) -> list[Fact]:
    async with pool.connection() as connection:
        rows = await (await connection.execute(query, params)).fetchall()
    return [_fact_of(row) for row in rows]


def _fact_of(row: DictRow) -> Fact:
    supersedes = row["supersedes"]
    return Fact(
        id=str(row["id"]),
        contact=row["contact"],
        category=row["category"],
        text=row["text"],
        valid_from=row["valid_from"],
        invalidated_at=row["invalidated_at"],
        supersedes=None if supersedes is None else str(supersedes),
        taught_by=row["source_call"],
        model=row["model"],
        confidence=row["confidence"],
    )


async def _added(
    connection: Connection, embedder: Embedder, params: Mapping[str, object], vector: list[float]
) -> Fact | None:
    written = {**params, "embedding": halfvec(vector), "model": embedder.embedding.model}
    query = UPDATED if params.get("of") is not None else ADDED
    row = await (await connection.execute(query, written)).fetchone()
    return None if row is None else _fact_of(row)


def _cursor_of(fact: Fact) -> str:
    return f"{fact.valid_from.isoformat()}|{fact.id}"


def _cursor(after: str | None) -> tuple[datetime, UUID] | None:
    if not after:
        return None
    moment, _, named = after.partition("|")
    try:
        return datetime.fromisoformat(moment), UUID(named)
    except ValueError as unread:
        raise DeclarationRefused(BAD_CURSOR.format(after=after)) from unread


def _like_escaped(words: str) -> str:
    return words.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _unaccented(text: str) -> str:
    letters = unicodedata.normalize("NFD", text)
    return folded("".join(item for item in letters if not unicodedata.combining(item)))
