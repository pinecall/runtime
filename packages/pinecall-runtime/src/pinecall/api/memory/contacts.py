"""A contact's memory: what the tenant reads, what the contact erases, what a golden judges."""

from __future__ import annotations

import time

from fastapi import APIRouter

from pinecall.api.deps import EmbedderDep, KeptMemoryDep, MemoryKeyDep
from pinecall.auth.keys import is_held_by
from pinecall.memory import DEFAULT_FACTS_PER_TURN, ask_golden
from pinecall.memory.scoring import Question, Score, score_golden
from pinecall.types import Fact
from pinecall_protocol.rest import (
    ContactFact,
    ContactMemory,
    Forgotten,
    MemoryGolden,
    MemoryMiss,
    MemoryScore,
)

router = APIRouter()


# The history, not the current facts: every row memory ever held about this contact, the current
# ones first, then what they superseded — with the two dates that bound each. What a turn reads
# is the `recall` tool; this is what a person reads when the contact asks what is known.
@router.get("/v1/contacts/{contact}/memory")
async def history(contact: str, key: MemoryKeyDep, memory: KeptMemoryDep) -> ContactMemory:
    """Everything memory ever kept about one contact of this org, current facts first."""
    facts = await memory.history(key.org, key.env, is_held_by(key), contact)
    return ContactMemory(facts=[_on_the_wire(fact) for fact in facts])


# The one DELETE memory has: every row of the contact at once, superseded ones included, because
# the right to be forgotten is not the right to have the current version forgotten.
@router.delete("/v1/contacts/{contact}/memory")
async def forget(contact: str, key: MemoryKeyDep, memory: KeptMemoryDep) -> Forgotten:
    """Every fact of the contact, gone; how many went. Zero is a fine answer, not a 404."""
    return Forgotten(forgotten=await memory.forget(key.org, key.env, is_held_by(key), contact))


# The golden's own facts, written to a scratch contact and asked: memory/golden_runs.py.
@router.post("/v1/contacts/memory/eval")
async def evaluate(
    said: MemoryGolden, key: MemoryKeyDep, memory: KeptMemoryDep, embedder: EmbedderDep
) -> MemoryScore:
    """Every question of the golden asked of memory, and how well it ranked the facts."""
    k = said.k or DEFAULT_FACTS_PER_TURN
    questions = [
        Question(holds=one.holds, asks=one.asks, expects=one.expects) for one in said.questions
    ]
    started = time.perf_counter()
    answered = await ask_golden(memory, key.org, key.env, is_held_by(key), questions, k=k)
    took_ms = (time.perf_counter() - started) * 1000
    return _as_a_score(await embedder.model(), score_golden(answered, k), took_ms)


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
