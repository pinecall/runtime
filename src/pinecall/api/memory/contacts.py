"""A contact's memory: what the tenant reads, what the contact erases, what a golden judges."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter

from pinecall.api.deps import EmbedderDep, KeptMemoryDep, MemoryKeyDep
from pinecall.auth.keys import held_by
from pinecall.memory import DEFAULT_FACTS_PER_TURN, Memory
from pinecall.memory.scoring import Answered, Question, Score, scored
from pinecall.types import Env, Fact
from pinecall_protocol.rest import (
    ContactFact,
    ContactMemory,
    Forgotten,
    MemoryGolden,
    MemoryMiss,
    MemoryQuestion,
    MemoryScore,
)

router = APIRouter()


# The history, not the current facts: every row memory ever held about this contact, the current
# ones first, then what they superseded — with the two dates that bound each. What a turn reads
# is the `recall` tool; this is what a person reads when the contact asks what is known.
@router.get("/v1/contacts/{contact}/memory")
async def history(contact: str, key: MemoryKeyDep, memory: KeptMemoryDep) -> ContactMemory:
    """Everything memory ever kept about one contact of this org, current facts first."""
    facts = await memory.history(key.org, key.env, held_by(key), contact)
    return ContactMemory(facts=[_on_the_wire(fact) for fact in facts])


# The one DELETE memory has: every row of the contact at once, superseded ones included, because
# the right to be forgotten is not the right to have the current version forgotten.
@router.delete("/v1/contacts/{contact}/memory")
async def forget(contact: str, key: MemoryKeyDep, memory: KeptMemoryDep) -> Forgotten:
    """Every fact of the contact, gone; how many went. Zero is a fine answer, not a 404."""
    return Forgotten(forgotten=await memory.forget(key.org, key.env, held_by(key), contact))


# A golden is the only thing that can say memory returned the WRONG facts: the judge that runs on
# every call weighs what the agent said against the facts it was handed, and never sees the better
# one that was missed. The golden brings its own facts, so no contact of this org is read or
# touched — they are written to a scratch contact, asked, and deleted. Writing them is what makes
# the figures the real ranking: the same two index scans, the same fusion, the same embedder as a
# turn. Scoring a list in memory would have measured an arithmetic of ours instead of the index.
# docs/retrieval/spec.md.
@router.post("/v1/contacts/memory/eval")
async def evaluate(
    said: MemoryGolden, key: MemoryKeyDep, memory: KeptMemoryDep, embedder: EmbedderDep
) -> MemoryScore:
    """Every question of the golden asked of memory, and how well it ranked the facts."""
    k = said.k or DEFAULT_FACTS_PER_TURN
    # A contact nobody has and nobody can collide with, for the length of one request. The rows
    # are gone whatever happens, which is why the delete is in a finally and not after the loop.
    scratch = f"golden-{uuid4().hex}"
    started = time.perf_counter()
    try:
        answered = [
            await _asked(memory, key.org, key.env, held_by(key), scratch, one, k=k)
            for one in said.questions
        ]
    finally:
        await memory.forget(key.org, key.env, held_by(key), scratch)
    took_ms = (time.perf_counter() - started) * 1000
    return _as_a_score(await embedder.model(), scored(answered, k), took_ms)


# One question at a time and the contact emptied between them: what memory holds for a question is
# that question's own list and never what another one wrote. Every fact is written at the same
# moment, so the recency weighing treats them alike and what a golden measures is the words and
# the meaning — a golden that carried dates would be asking a different question.
async def _asked(
    memory: Memory,
    org: str,
    env: Env,
    holder: str | None,
    scratch: str,
    question: MemoryQuestion,
    *,
    k: int,
) -> Answered:
    """One question's facts written, recalled and cleared; what came back, best first."""
    await memory.hold(org, env, holder, scratch, question.holds, at=datetime.now(UTC))
    facts = await memory.recall(org, env, holder, scratch, question.asks, k=k)
    await memory.forget(org, env, holder, scratch)
    return Answered(
        question=Question(holds=question.holds, asks=question.asks, expects=question.expects),
        facts=facts,
    )


def _as_a_score(model: str, score: Score, took_ms: float) -> MemoryScore:
    """The figures and every miss, as the wire carries them."""
    return MemoryScore(
        model=model,
        questions=score.questions,
        k=score.k,
        recall_at_k=score.recall_at_k,
        ndcg_at_10=score.ndcg_at_10,
        took_ms=took_ms,
        misses=[
            MemoryMiss(asks=one.question.asks, missing=list(one.missing), found=list(one.found))
            for one in score.misses
        ],
    )


def _on_the_wire(fact: Fact) -> ContactFact:
    """One row as the door says it: the fact, and since when and until when it held."""
    return ContactFact(
        id=fact.id,
        text=fact.text,
        category=fact.category,
        source=fact.source,
        valid_from=fact.valid_from.timestamp(),
        invalidated_at=None if fact.invalidated_at is None else fact.invalidated_at.timestamp(),
    )
