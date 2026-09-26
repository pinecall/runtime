"""A memory golden asked of the real index: its facts held under a scratch contact, then gone."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

from pinecall.memory.protocol import Memory
from pinecall.memory.scoring import Answered, Question
from pinecall.types import Env


# A golden is the only thing that can say memory returned the WRONG facts: the judge that runs on
# every call weighs what the agent said against the facts it was handed, and never sees the better
# one that was missed. The golden brings its own facts, so no contact of this org is read or
# touched — they are written to a scratch contact, asked, and deleted. Writing them is what makes
# the figures the real ranking: the same two index scans, the same fusion, the same embedder as a
# turn. Scoring a list in memory would have measured an arithmetic of ours instead of the index.
# docs/retrieval/spec.md.
async def ask_golden(
    memory: Memory,
    org: str,
    env: Env,
    holder: str | None,
    questions: Sequence[Question],
    *,
    k: int,
) -> list[Answered]:
    """Every question asked of memory in turn, each on its own facts; what came back, best first."""
    # A contact nobody has and nobody can collide with, for the length of one request. The rows
    # are gone whatever happens, which is why the delete is in a finally and not after the loop.
    scratch = f"golden-{uuid4().hex}"
    try:
        return [
            await _asked(memory, org, env, holder, scratch, question, k=k) for question in questions
        ]
    finally:
        await memory.forget(org, env, holder, scratch)


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
    question: Question,
    *,
    k: int,
) -> Answered:
    """One question's facts written, recalled and cleared; what came back, best first."""
    await memory.hold(org, env, holder, scratch, question.holds, at=datetime.now(UTC))
    facts = await memory.recall(org, env, holder, scratch, question.asks, k=k)
    await memory.forget(org, env, holder, scratch)
    return Answered(question=question, facts=facts)
