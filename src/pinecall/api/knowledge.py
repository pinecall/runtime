"""The knowledge base's doors: a tenant pushes a base whole, lists its bases, drops one."""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException

from pinecall.api._deps import AdmissionDep, KeptKnowledgeDep, KnowledgeKeyDep
from pinecall.knowledge.scoring import Answered, Question, Score, scored
from pinecall.orgs.admission import QuotaExhausted
from pinecall.types import KnowledgeFile
from pinecall.types.knowledge import DEFAULT_CHUNKS_PER_TURN
from pinecall_protocol.rest import (
    GoldenMiss,
    KnowledgeBase,
    KnowledgeGolden,
    KnowledgeList,
    KnowledgePush,
    KnowledgePushed,
    KnowledgeScore,
)

router = APIRouter()

# Dropping a name nobody pushed is a typo, and a verb that answered yes to it would send the
# tenant looking for the change somewhere else. 404, and the org is never named: it is the key's.
NO_SUCH_BASE = "no knowledge base named {base}: nothing was pushed under that name"

# A drop has nothing to say back. The same number every removal in this runtime answers.
NO_BODY = 204


# The org is the key's, on every verb here: a tenant pushes into its own tables and reads its own
# list, and a key that could name another org's base would be a key that could read its files.
# A push replaces the base whole — the tenant's folder as of now — in one transaction, and the
# TEI behind the embedder is reached here for the first time on a gateway that never ran a
# lookup: a push on a box with no TEI is a 5xx that names TEI, never a half-written base.
@router.put("/v1/knowledge/{base}")
async def push(
    base: str,
    said: KnowledgePush,
    key: KnowledgeKeyDep,
    knowledge: KeptKnowledgeDep,
    admission: AdmissionDep,
) -> KnowledgePushed:
    """The tenant's files as this base, chunked, embedded and indexed; how many chunks, how long."""
    started = time.perf_counter()
    files = [KnowledgeFile(path=file.path, text=file.text) for file in said.files]
    # What the org would keep once this push has landed: its OTHER bases, plus what these files
    # become — the base being replaced is freed by the push itself, so it is not counted twice.
    # Judged before a row is written, because a push is one statement and all or nothing.
    keeping = await knowledge.kept(key.org, besides=base) + knowledge.how_many_chunks(files)
    try:
        await admission.a_push(key.org, keeping)
    except QuotaExhausted as refused:
        # 429 and the quota's own sentence, as every call door answers one: `pinecall knowledge
        # push` prints it, and it names both figures — what this would keep, and what the cap is.
        raise HTTPException(429, str(refused)) from refused
    chunks = await knowledge.put(key.org, base, files)
    return KnowledgePushed(base=base, chunks=chunks, took_ms=(time.perf_counter() - started) * 1000)


@router.get("/v1/knowledge")
async def bases(key: KnowledgeKeyDep, knowledge: KeptKnowledgeDep) -> KnowledgeList:
    """Every base this org has pushed: its name, its size, when."""
    return KnowledgeList(
        bases=[
            KnowledgeBase(
                base=one.base,
                chunks=one.chunks,
                model=one.model,
                pushed_at=one.pushed_at.timestamp(),
            )
            for one in await knowledge.bases(key.org)
        ]
    )


@router.delete("/v1/knowledge/{base}", status_code=NO_BODY)
async def drop(base: str, key: KnowledgeKeyDep, knowledge: KeptKnowledgeDep) -> None:
    """The base and every chunk of it, gone. 404 when the org never pushed one by that name."""
    if not await knowledge.drop(key.org, base):
        raise HTTPException(status_code=404, detail=NO_SUCH_BASE.format(base=base))


# A golden is the only thing that can say the index MISSED a better passage, because the judge that
# runs on every call can only weigh what the model was given. Both figures are computed here, by
# code, with no model in the loop: two runs of one golden over one base answer the same numbers,
# which is what makes "we changed the embedder" a sentence with a figure after it.
# docs/retrieval/spec.md.
@router.post("/v1/knowledge/{base}/eval")
async def evaluate(
    base: str, said: KnowledgeGolden, key: KnowledgeKeyDep, knowledge: KeptKnowledgeDep
) -> KnowledgeScore:
    """Every question of the golden asked of the base, and how well it ranked the answers."""
    held = next((one for one in await knowledge.bases(key.org) if one.base == base), None)
    if held is None:
        raise HTTPException(status_code=404, detail=NO_SUCH_BASE.format(base=base))
    k = said.k or DEFAULT_CHUNKS_PER_TURN
    started = time.perf_counter()
    # Asked in order and one at a time: a golden is run when somebody changed something, never on
    # a caller's clock, and a hundred concurrent searches would measure the pool and not the index.
    answered = [
        Answered(
            question=Question(asks=one.asks, expects=one.expects),
            # No min_score: a golden asks where the passage RANKED, and a threshold would answer
            # a different question — whether it also cleared the bar the agent happens to set.
            chunks=await knowledge.search(key.org, base, one.asks, k=k),
        )
        for one in said.questions
    ]
    return _as_a_score(
        base, held.model, scored(answered, k), (time.perf_counter() - started) * 1000
    )


def _as_a_score(base: str, model: str, score: Score, took_ms: float) -> KnowledgeScore:
    """The figures and every miss, as the wire carries them."""
    return KnowledgeScore(
        base=base,
        model=model,
        questions=score.questions,
        k=score.k,
        recall_at_k=score.recall_at_k,
        ndcg_at_10=score.ndcg_at_10,
        took_ms=took_ms,
        misses=[
            GoldenMiss(asks=one.question.asks, expects=one.question.expects, found=list(one.found))
            for one in score.misses
        ],
    )
