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
from pinecall.memory.statements import (
    ADD,
    BY_VECTOR,
    BY_WORDS,
    CURRENT,
    ENDED,
    EVERY_ROW,
    FORGET,
    INVALIDATE,
    KEPT,
    TAUGHT_BY,
    UPDATE,
)
from pinecall.providers.embedder import Embedder, as_halfvec
from pinecall.providers.models import Models
from pinecall.types import (
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
        held = (org, env, whose(holder), contact, as_of)
        # The words need no vector: their branch runs while the query is at the embedder, which
        # is most of a recall's budget. One group, so the other branch is cancelled if one breaks.
        async with asyncio.TaskGroup() as branches:
            sparse = branches.create_task(self._pool.fetch(BY_WORDS, *held, query))
            dense = branches.create_task(self._nearest(held, query))
        return ranked(
            [_a_candidate(row) for row in dense.result()],
            [_a_candidate(row) for row in sparse.result()],
            now=as_of or datetime.now(UTC),
            k=k,
        )

    async def _nearest(self, held: tuple[Any, ...], query: str) -> Sequence[Mapping[str, Any]]:
        """The dense branch: the query embedded, then the rows nearest to it."""
        vector = await self._embedded(query)
        return await self._pool.fetch(BY_VECTOR, *held, vector, await self._embedder.model())

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
                for row in await self._pool.fetch(CURRENT, org, env, whose(holder), contact)
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
        rows = [
            (org, env, whose(holder), contact, text, None, vector, at, None, model)
            for text, vector in zip(facts, vectors, strict=True)
        ]
        # All of them or none: a golden's facts are one state a call starts from.
        async with self._pool.acquire() as connection, connection.transaction():
            await connection.executemany(ADD, rows)

    async def forget(self, org: str, env: Env, holder: str | None, contact: str) -> int:
        """One DELETE, and how many rows went."""
        row = await self._pool.fetchrow(FORGET, org, env, whose(holder), contact)
        return 0 if row is None else int(row["forgotten"])

    async def history(self, org: str, env: Env, holder: str | None, contact: str) -> list[Fact]:
        """Every row, the current ones first and the newest of each group before the older."""
        return [
            _a_fact(row)
            for row in await self._pool.fetch(EVERY_ROW, org, env, whose(holder), contact)
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
            TAUGHT_BY,
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
        """One UPDATE; the row RETURNING says whether a current fact answered."""
        return await self._pool.fetchrow(ENDED, org, env, whose(holder), id, at) is not None

    async def kept(self, org: str) -> int:
        """One count over the partial index: the facts that hold right now, across the org."""
        row = await self._pool.fetchrow(KEPT, org)
        return 0 if row is None else int(row["kept"])

    # The sentences are embedded in one batch before any row is written; then each op is one
    # statement, in the order the model gave them, all inside one transaction — a failure between
    # two left a fact ended and its replacement never written (2026-09-26). The facts handed back
    # are the rows that now hold: an add, or the new row of an update. An invalidation writes an
    # end and no row.
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
        async with self._pool.acquire() as connection, connection.transaction():
            for op in ops:
                if op.op == "invalidate":
                    await connection.execute(
                        INVALIDATE, org, env, whose(holder), contact, at, op.of
                    )
                    continue
                statement = ADD if op.op == "add" else UPDATE
                named = (op.of,) if op.op == "update" else ()
                row = await connection.fetchrow(
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
