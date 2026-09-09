"""PgvectorMemory: the contact's facts in Postgres, hybrid recall, bi-temporal writes."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from pgvector import HalfVector

from pinecall.log.store import Pool
from pinecall.memory.extraction import OPS_THAT_WRITE, Op, extracted
from pinecall.memory.protocol import DEFAULT_FACTS_PER_TURN, Spoken
from pinecall.memory.ranking import CANDIDATES_PER_BRANCH, Candidate, ranked
from pinecall.providers.embedder import Embedder
from pinecall.providers.models import Models
from pinecall.types import Channel, Fact, MemoryPolicy, Model, ProviderKeys
from pinecall_protocol.defs import MemoryFact, MemoryOp

# ── the statements ──────────────────────────────────────────────────────────────

# The columns a Fact is read off, spelled once for the four reads below.
_COLUMNS = "id, contact, text, category, source_call, valid_from, invalidated_at, confidence"

# What "current" means for a recall: with no as_of, the rows nobody invalidated — the partial
# index's own WHERE; with one, the rows that held at that moment, which is the bi-temporal read.
# kinds is NULL when the marker named none, and the category filter is then no filter at all.
_HELD = """
WHERE org = $1 AND contact = $2
  AND (($3::timestamptz IS NULL AND invalidated_at IS NULL)
       OR ($3::timestamptz IS NOT NULL AND valid_from <= $3
           AND (invalidated_at IS NULL OR invalidated_at > $3)))
  AND ($4::text[] IS NULL OR category = ANY($4::text[]))
"""

# The dense branch: cosine over the halved vectors, the HNSW index's own operator class.
_BY_VECTOR = f"""
SELECT {_COLUMNS} FROM contact_memories
{_HELD}
ORDER BY embedding <=> $5::text::halfvec
LIMIT {CANDIDATES_PER_BRANCH}
"""

# The sparse branch: pg_textsearch's `<@>` is the NEGATIVE BM25 score, lower is better, and a row
# with no term in common with the query is not returned at all. The query is parsed against the
# named index, whose text configuration (0008: spanish) stems both sides the same way.
_BY_WORDS = f"""
SELECT {_COLUMNS} FROM contact_memories
{_HELD}
ORDER BY text <@> to_bm25query($5, 'contact_memories_text_bm25')
LIMIT {CANDIDATES_PER_BRANCH}
"""

_CURRENT = f"""
SELECT {_COLUMNS} FROM contact_memories
WHERE org = $1 AND contact = $2 AND invalidated_at IS NULL
ORDER BY valid_from, id
"""

_EVERY_ROW = f"""
SELECT {_COLUMNS} FROM contact_memories
WHERE org = $1 AND contact = $2
ORDER BY (invalidated_at IS NULL) DESC, valid_from DESC, id
"""

_FORGET = "DELETE FROM contact_memories WHERE org = $1 AND contact = $2"

_ADD = """
INSERT INTO contact_memories (org, contact, text, category, embedding, valid_from, source_call)
VALUES ($1, $2, $3, $4, $5::text::halfvec, $6, $7)
RETURNING id
"""

# An update is a new row that supersedes the old one, and the old one's end is written in the
# same statement: the two never disagree, and a fact somebody already superseded takes no row.
_UPDATE = """
WITH superseded AS (
    UPDATE contact_memories SET invalidated_at = $6
    WHERE id = $8::uuid AND org = $1 AND contact = $2 AND invalidated_at IS NULL
    RETURNING id
)
INSERT INTO contact_memories
    (org, contact, text, category, embedding, valid_from, source_call, supersedes)
SELECT $1, $2, $3, $4, $5::text::halfvec, $6, $7, superseded.id FROM superseded
RETURNING id
"""

_INVALIDATE = """
UPDATE contact_memories SET invalidated_at = $3
WHERE id = $4::uuid AND org = $1 AND contact = $2 AND invalidated_at IS NULL
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
        contact: str,
        query: str,
        *,
        kinds: Sequence[str] = (),
        k: int = DEFAULT_FACTS_PER_TURN,
        as_of: datetime | None = None,
    ) -> list[Fact]:
        """Dense and BM25 over the contact's held facts, fused by rank, the best k at 0..1."""
        vector = await self._embedded(query)
        held = (org, contact, as_of, list(kinds) or None)
        dense, sparse = await asyncio.gather(
            self._pool.fetch(_BY_VECTOR, *held, vector), self._pool.fetch(_BY_WORDS, *held, query)
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
        contact: str,
        turns: Sequence[Spoken],
        *,
        channel: Channel,
        at: datetime,
        policy: MemoryPolicy,
        llm: Model | None,
        keys: ProviderKeys,
        call: str | None = None,
    ) -> list[MemoryOp]:
        """What the call taught, written; one op on the wire carrying the facts now held."""
        started = time.perf_counter()
        written: list[Fact] = []
        # A tenant that named nothing worth keeping keeps nothing, and pays for no model call.
        if policy.remember:
            known = [_a_fact(row) for row in await self._pool.fetch(_CURRENT, org, contact)]
            chat = self._models(llm, keys)
            try:
                ops = await extracted(
                    chat, known=known, turns=turns, policy=policy, channel=channel
                )
            finally:
                await chat.aclose()
            written = await self._applied(org, contact, ops, at=at, call=call)
        took_ms = (time.perf_counter() - started) * 1000
        return [
            MemoryOp(
                op="remember",
                contact=contact,
                facts=[_on_the_wire(fact) for fact in written],
                took_ms=took_ms,
            )
        ]

    async def forget(self, org: str, contact: str) -> int:
        """One DELETE, and the count off its command tag."""
        tag = await self._pool.execute(_FORGET, org, contact)
        return int(tag.split()[-1])

    async def history(self, org: str, contact: str) -> list[Fact]:
        """Every row, the current ones first and the newest of each group before the older."""
        return [_a_fact(row) for row in await self._pool.fetch(_EVERY_ROW, org, contact)]

    # The sentences are embedded in one batch before any row is written; then each op is one
    # statement, in the order the model gave them. The facts handed back are the rows that now
    # hold: an add, or the new row of an update. An invalidation writes an end and no row.
    async def _applied(
        self, org: str, contact: str, ops: Sequence[Op], *, at: datetime, call: str | None
    ) -> list[Fact]:
        """Every op against the table; the rows that were added, as Facts."""
        writing = [op for op in ops if op.op in OPS_THAT_WRITE]
        embedded = await self._embedded_all([op.text for op in writing])
        vectors = dict(zip(writing, embedded, strict=True))
        written: list[Fact] = []
        for op in ops:
            if op.op == "invalidate":
                await self._pool.execute(_INVALIDATE, org, contact, at, op.of)
                continue
            statement = _ADD if op.op == "add" else _UPDATE
            named = (op.of,) if op.op == "update" else ()
            row = await self._pool.fetchrow(
                statement, org, contact, op.text, op.category, vectors[op], at, call, *named
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
        return [HalfVector(vector).to_text() for vector in await self._embedder.embed(listed)]


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
