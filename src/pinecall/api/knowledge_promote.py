"""Promote a base: the sandbox's rows into production, once the golden says recall did not drop."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from pinecall.api._deps import KeptKnowledgeDep, KnowledgeKeyDep
from pinecall.auth.keys import held_by
from pinecall.knowledge import Knowledge
from pinecall.knowledge.scoring import Answered, Question, scored
from pinecall.types import PRODUCTION, SANDBOX, THE_ORGS_OWN, Env
from pinecall.types.knowledge import DEFAULT_CHUNKS_PER_TURN
from pinecall_protocol.rest import KnowledgeGolden, KnowledgePromote, KnowledgePromoted

router = APIRouter()

FROM_THE_SANDBOX = "a base is promoted from the sandbox, and this key's world is production"
NOTHING_TO_PROMOTE = "no knowledge base named {base} in the sandbox: nothing to promote"
RECALL_DROPPED = (
    "the golden's recall@{k} over {base} would drop from {before:.2f} to {after:.2f}: "
    "production keeps what it has"
)


# The rows a golden ranked are the rows production gets: `copy` moves vectors and all, and no
# embedder runs between the figure and the telephone. The gate is the golden the body carries,
# asked of both copies — production's as it stands, the sandbox's about to replace it — and a
# recall that would fall is refused with both figures. No golden sent: the copy is made and both
# recalls are null, because the base may be new and a tenant pushes its first golden later.
@router.post("/v1/knowledge/{base}/promote")
async def promote(
    base: str, said: KnowledgePromote, key: KnowledgeKeyDep, knowledge: KeptKnowledgeDep
) -> KnowledgePromoted:
    """The sandbox's base as production's, gated by the golden when one was sent."""
    if key.env != SANDBOX:
        raise HTTPException(status_code=409, detail=FROM_THE_SANDBOX)
    corner = held_by(key)
    held = await knowledge.bases(key.org, SANDBOX, corner)
    if not any(one.base == base for one in held):
        raise HTTPException(status_code=404, detail=NOTHING_TO_PROMOTE.format(base=base))
    before = after = None
    if said.golden is not None:
        k = said.golden.k or DEFAULT_CHUNKS_PER_TURN
        standing = await knowledge.bases(key.org, PRODUCTION, THE_ORGS_OWN)
        if any(one.base == base for one in standing):
            before = await _recall(knowledge, key.org, PRODUCTION, None, base, said.golden, k)
        after = await _recall(knowledge, key.org, SANDBOX, corner, base, said.golden, k)
        if before is not None and after < before:
            raise HTTPException(
                status_code=409,
                detail=RECALL_DROPPED.format(k=k, base=base, before=before, after=after),
            )
    chunks = await knowledge.copy(key.org, SANDBOX, corner, base, PRODUCTION)
    return KnowledgePromoted(
        base=base, world=PRODUCTION, chunks=chunks, recall_before=before, recall_after=after
    )


async def _recall(
    knowledge: Knowledge,
    org: str,
    env: Env,
    holder: str | None,
    base: str,
    golden: KnowledgeGolden,
    k: int,
) -> float:
    """recall@k of this golden over this corner's copy of the base, one question at a time."""
    answered = [
        Answered(
            question=Question(asks=one.asks, expects=one.expects),
            chunks=await knowledge.search(org, env, holder, base, one.asks, k=k),
        )
        for one in golden.questions
    ]
    return scored(answered, k).recall_at_k
