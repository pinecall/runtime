"""PgvectorMemory: the contact's facts in Postgres, hybrid recall, bi-temporal writes."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pinecall.log.store import Pool
from pinecall.log.store.index import like_escaped
from pinecall.memory.extraction import OPS_THAT_WRITE, Op, extracted
from pinecall.memory.protocol import DEFAULT_FACTS_PER_TURN, FactsPage, Spoken
from pinecall.memory.ranking import Candidate, ranked
from pinecall.providers.embedder import Embedder, as_halfvec
from pinecall.providers.models import Models
from pinecall.types import (
    CANDIDATES_PER_BRANCH,
    Brought,
    Channel,
    Env,
    Fact,
    MemoryPolicy,
    Model,
    ToolSpec,
    whose,
)
from pinecall_protocol.defs import MemoryFact, MemoryOp

# ── the statements ──────────────────────────────────────────────────────────────

# The columns a Fact is read off, spelled once for the four reads below.
_COLUMNS = "id, contact, text, category, source_call, valid_from, invalidated_at, confidence"

# What "current" means for a recall: with no as_of, the rows nobody invalidated — the partial
# index's own WHERE; with one, the rows that held at that moment, which is the bi-temporal read.
_HELD = """
WHERE org = $1 AND env = $2 AND holder = $3 AND contact = $4
  AND (($5::timestamptz IS NULL AND invalidated_at IS NULL)
       OR ($5::timestamptz IS NOT NULL AND valid_from <= $5
           AND (invalidated_at IS NULL OR invalidated_at > $5)))
"""

# The dense branch: cosine over the halved vectors, the HNSW index's own operator class, and only
# over the rows THIS embedder wrote — a vector of another model is a number of the same width and
# nothing more. The words branch below carries no such filter on purpose: BM25 reads the text and
# not the vector, so a fact written under an older embedder is still recalled by what it says.
_BY_VECTOR = f"""
SELECT {_COLUMNS} FROM contact_memories
{_HELD}
  AND model = $7
ORDER BY embedding <=> $6::text::halfvec
LIMIT {CANDIDATES_PER_BRANCH}
"""

# The sparse branch: pg_textsearch's `<@>` is the NEGATIVE BM25 score, lower is better, and a row
# with no term in common with the query is not returned at all. The query is parsed against the
# named index, whose text configuration (0008: spanish) stems both sides the same way.
_BY_WORDS = f"""
SELECT {_COLUMNS} FROM contact_memories
{_HELD}
ORDER BY text <@> to_bm25query($6, 'contact_memories_text_bm25')
LIMIT {CANDIDATES_PER_BRANCH}
"""

_CURRENT = f"""
SELECT {_COLUMNS} FROM contact_memories
WHERE org = $1 AND env = $2 AND holder = $3 AND contact = $4 AND invalidated_at IS NULL
ORDER BY valid_from, id
"""

_EVERY_ROW = f"""
SELECT {_COLUMNS} FROM contact_memories
WHERE org = $1 AND env = $2 AND holder = $3 AND contact = $4
ORDER BY (invalidated_at IS NULL) DESC, valid_from DESC, id
"""

# What an agent's calls taught, across its contacts: the fact's own source call is a head row, and
# that row says which agent took it. Current facts only, newest first; `$5` is the words as a LIKE
# pattern, `$6`/`$7` the cursor — the last fact of the page before.
_TAUGHT_BY = f"""
SELECT {_COLUMNS}, head.agent AS taught_by FROM contact_memories memory
JOIN call_log_head head ON head.log = memory.source_call
WHERE memory.org = $1 AND memory.env = $2 AND memory.holder = $3
  AND ($4::text IS NULL OR head.agent = $4)
  AND memory.invalidated_at IS NULL
  AND ($5::text IS NULL OR memory.text ILIKE '%' || $5 || '%'
       OR memory.contact ILIKE '%' || $5 || '%' OR memory.category ILIKE '%' || $5 || '%')
  AND ($6::timestamptz IS NULL OR (memory.valid_from, memory.id) < ($6, $7::uuid))
ORDER BY memory.valid_from DESC, memory.id DESC
LIMIT $8
"""

_ENDED = """
UPDATE contact_memories SET invalidated_at = $5
WHERE id = $4::uuid AND org = $1 AND env = $2 AND holder = $3 AND invalidated_at IS NULL
"""

_FORGET = (
    "DELETE FROM contact_memories WHERE org = $1 AND env = $2 AND holder = $3 AND contact = $4"
)

# What the org KEEPS, which is what its quota is about: the current rows, every contact together,
# in BOTH worlds — a fact a test call wrote is a row on the same disk as one a real call wrote. A
# superseded row is history and not a fact the org holds, so the count reads the same partial
# index (org, env, contact) WHERE invalidated_at IS NULL that a recall does, over every env.
_KEPT = "SELECT count(*) AS kept FROM contact_memories WHERE org = $1 AND invalidated_at IS NULL"

_ADD = """
INSERT INTO contact_memories
    (org, env, holder, contact, text, category, embedding, valid_from, source_call, model)
VALUES ($1, $2, $3, $4, $5, $6, $7::text::halfvec, $8, $9, $10)
RETURNING id
"""

# An update is a new row that supersedes the old one, and the old one's end is written in the
# same statement: the two never disagree, and a fact somebody already superseded takes no row.
_UPDATE = """
WITH superseded AS (
    UPDATE contact_memories SET invalidated_at = $8
    WHERE id = $11::uuid AND org = $1 AND env = $2 AND holder = $3 AND contact = $4
      AND invalidated_at IS NULL
    RETURNING id
)
INSERT INTO contact_memories
    (org, env, holder, contact, text, category, embedding, valid_from, source_call, model,
     supersedes)
SELECT $1, $2, $3, $4, $5, $6, $7::text::halfvec, $8, $9, $10, superseded.id FROM superseded
RETURNING id
"""

_INVALIDATE = """
UPDATE contact_memories SET invalidated_at = $5
WHERE id = $6::uuid AND org = $1 AND env = $2 AND holder = $3 AND contact = $4
  AND invalidated_at IS NULL
"""


class PgvectorMemory:
    """The Memory in Postgres: the table 0008 made, the embedder for both sides, the org's model."""

    def __init__(self, pool: Pool, embedder: Embedder, models: Models) -> None:
        self._pool = pool
        self._embedder = embedder
        self._models = models

    # Two queries at once, one per branch, and the fusion in memory: the whole of a recall is
    # two index scans and no model, which is what keeps it under the turn's budget.
    async def recall(
        self,
        org: str,
        env: Env,
        holder: str | None,
        contact: str,
        query: str,
        *,
        k: int = DEFAULT_FACTS_PER_TURN,
        as_of: datetime | None = None,
    ) -> list[Fact]:
        """Dense and BM25 over the contact's held facts, fused by rank, the best k at 0..1."""
        vector = await self._embedded(query)
        held = (org, env, whose(holder), contact, as_of)
        dense, sparse = await asyncio.gather(
            self._pool.fetch(_BY_VECTOR, *held, vector, await self._embedder.model()),
            self._pool.fetch(_BY_WORDS, *held, query),
        )
        return ranked(
            [_a_candidate(row) for row in dense],
            [_a_candidate(row) for row in sparse],
            now=as_of or datetime.now(UTC),
            k=k,
        )

    async def remember(
        self,
        org: str,
        env: Env,
        holder: str | None,
        contact: str,
        turns: Sequence[Spoken],
        *,
        channel: Channel,
        at: datetime,
        policy: MemoryPolicy,
        llm: Model | None,
        brought: Brought,
        call: str | None = None,
        tools: Sequence[ToolSpec] = (),
    ) -> list[MemoryOp]:
        """What the call taught, written; one op on the wire carrying the facts now held."""
        started = time.perf_counter()
        written: list[Fact] = []
        # A tenant that named nothing worth keeping keeps nothing, and pays for no model call.
        if policy.remember:
            known = [
                _a_fact(row)
                for row in await self._pool.fetch(_CURRENT, org, env, whose(holder), contact)
            ]
            chat = self._models(llm, brought)
            try:
                ops = await extracted(
                    chat, known=known, turns=turns, policy=policy, channel=channel, tools=tools
                )
            finally:
                await chat.aclose()
            written = await self._applied(org, env, holder, contact, ops, at=at, call=call)
        took_ms = (time.perf_counter() - started) * 1000
        return [
            MemoryOp(
                op="remember",
                contact=contact,
                facts=[_on_the_wire(fact) for fact in written],
                took_ms=took_ms,
            )
        ]

    # Every sentence embedded in one batch, then one INSERT each, all at the same moment: nothing
    # is superseded and nothing is asked of a model, so what lands is exactly what was given. The
    # confidence is the column's own default, which is what a fact nobody weighed is worth.
    async def hold(
        self,
        org: str,
        env: Env,
        holder: str | None,
        contact: str,
        facts: Sequence[str],
        *,
        at: datetime,
    ) -> None:
        """These sentences as the contact's facts, with this embedder's name beside each vector."""
        vectors = await self._embedded_all(facts)
        model = await self._embedder.model()
        for text, vector in zip(facts, vectors, strict=True):
            await self._pool.execute(
                _ADD, org, env, whose(holder), contact, text, None, vector, at, None, model
            )

    async def forget(self, org: str, env: Env, holder: str | None, contact: str) -> int:
        """One DELETE, and the count off its command tag."""
        tag = await self._pool.execute(_FORGET, org, env, whose(holder), contact)
        return int(tag.split()[-1])

    async def history(self, org: str, env: Env, holder: str | None, contact: str) -> list[Fact]:
        """Every row, the current ones first and the newest of each group before the older."""
        return [
            _a_fact(row)
            for row in await self._pool.fetch(_EVERY_ROW, org, env, whose(holder), contact)
        ]

    async def taught_by(
        self,
        org: str,
        env: Env,
        holder: str | None,
        agent: str | None,
        *,
        words: str | None,
        after: str | None,
        limit: int,
    ) -> FactsPage:
        """One page and one row past it, to know whether there is another."""
        cursor = a_cursor(after)
        rows = await self._pool.fetch(
            _TAUGHT_BY,
            org,
            env,
            whose(holder),
            agent,
            None if not words else like_escaped(words),
            None if cursor is None else cursor[0],
            None if cursor is None else cursor[1],
            limit + 1,
        )
        facts = [_a_fact(row) for row in rows]
        page = facts[:limit]
        more = cursor_of(page[-1]) if len(facts) > limit else None
        return FactsPage(page, more, {str(r["id"]): str(r["taught_by"]) for r in rows[:limit]})

    async def invalidated(
        self, org: str, env: Env, holder: str | None, id: str, at: datetime
    ) -> bool:
        """One UPDATE; the command tag says whether a current fact answered."""
        tag = await self._pool.execute(_ENDED, org, env, whose(holder), id, at)
        return tag.strip() != "UPDATE 0"

    async def kept(self, org: str) -> int:
        """One count over the partial index: the facts that hold right now, across the org."""
        row = await self._pool.fetchrow(_KEPT, org)
        return 0 if row is None else int(row["kept"])

    # The sentences are embedded in one batch before any row is written; then each op is one
    # statement, in the order the model gave them. The facts handed back are the rows that now
    # hold: an add, or the new row of an update. An invalidation writes an end and no row.
    async def _applied(
        self,
        org: str,
        env: Env,
        holder: str | None,
        contact: str,
        ops: Sequence[Op],
        *,
        at: datetime,
        call: str | None,
    ) -> list[Fact]:
        """Every op against the table; the rows that were added, as Facts."""
        writing = [op for op in ops if op.op in OPS_THAT_WRITE]
        embedded = await self._embedded_all([op.text for op in writing])
        vectors = dict(zip(writing, embedded, strict=True))
        # Written beside every vector, so a later recall can tell whose vectors these are without
        # asking the embedder about a row it did not make.
        model = await self._embedder.model()
        written: list[Fact] = []
        for op in ops:
            if op.op == "invalidate":
                await self._pool.execute(_INVALIDATE, org, env, whose(holder), contact, at, op.of)
                continue
            statement = _ADD if op.op == "add" else _UPDATE
            named = (op.of,) if op.op == "update" else ()
            row = await self._pool.fetchrow(
                statement,
                org,
                env,
                whose(holder),
                contact,
                op.text,
                op.category,
                vectors[op],
                at,
                call,
                model,
                *named,
            )
            if row is not None:
                written.append(
                    Fact(
                        id=str(row["id"]),
                        contact=contact,
                        text=op.text,
                        category=op.category,
                        source=call,
                        valid_from=at,
                        invalidated_at=None,
                        score=0.0,
                    )
                )
        return written

    async def _embedded(self, text: str) -> str:
        """One text as the halfvec literal a statement takes."""
        return (await self._embedded_all([text]))[0]

    async def _embedded_all(self, texts: Sequence[str]) -> list[str]:
        """Every text through the embedder once, each as a halfvec literal, in order."""
        listed = list(texts)
        if not listed:
            return []
        return [as_halfvec(vector) for vector in await self._embedder.embed(listed)]


def cursor_of(fact: Fact) -> str:
    """Where the page after this fact starts: when it was written, then which, since two may tie."""
    return f"{fact.valid_from.isoformat()}|{fact.id}"


def a_cursor(after: str | None) -> tuple[datetime, UUID] | None:
    """The moment and the id a cursor names, or None for a first page or a word that is not one."""
    if not after or "|" not in after:
        return None
    moment, id = after.split("|", 1)
    try:
        return datetime.fromisoformat(moment), UUID(id)
    except ValueError:
        return None


def _a_fact(row: Mapping[str, Any]) -> Fact:
    """One row as the shape both processes speak; the score is the ranking's to write."""
    return Fact(
        id=str(row["id"]),
        contact=str(row["contact"]),
        text=str(row["text"]),
        category=row["category"],
        source=row["source_call"],
        valid_from=row["valid_from"],
        invalidated_at=row["invalidated_at"],
        score=0.0,
    )


def _a_candidate(row: Mapping[str, Any]) -> Candidate:
    """One row as a branch found it, with the confidence the ranking weighs it by."""
    return Candidate(fact=_a_fact(row), confidence=float(row["confidence"]))


def _on_the_wire(fact: Fact) -> MemoryFact:
    """A written fact as the log carries it: no score, because nothing was asked."""
    return MemoryFact(id=fact.id, text=fact.text, category=fact.category, source=fact.source)
