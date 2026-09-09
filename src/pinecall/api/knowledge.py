"""The knowledge base's doors: a tenant pushes a base whole, lists its bases, drops one."""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException

from pinecall.api._deps import KeptKnowledgeDep, KeyDep
from pinecall.types import KnowledgeFile
from pinecall_protocol.rest import KnowledgeBase, KnowledgeList, KnowledgePush, KnowledgePushed

router = APIRouter()

# Dropping a name nobody pushed is a typo, and a verb that answered yes to it would send the
# tenant looking for the change somewhere else. 404, and the org is never named: it is the key's.
NO_SUCH_BASE = "no knowledge base named {base}: nothing was pushed under that name"

# A drop has nothing to say back. The same number every removal in this runtime answers.
NO_BODY = 204


# The org is the key's, on every verb here: a tenant pushes into its own tables and reads its own
# list, and a key that could name another org's base would be a key that could read its files.
# A push replaces the base whole — the tenant's folder as of now — in one transaction, and the
# TEI behind the embedder is reached here for the first time on a gateway that never filled a
# marker: a push on a box with no TEI is a 5xx that names TEI, never a half-written base.
@router.put("/v1/knowledge/{base}")
async def push(
    base: str, said: KnowledgePush, key: KeyDep, knowledge: KeptKnowledgeDep
) -> KnowledgePushed:
    """The tenant's files as this base, chunked, embedded and indexed; how many chunks, how long."""
    started = time.perf_counter()
    files = [KnowledgeFile(path=file.path, text=file.text) for file in said.files]
    chunks = await knowledge.put(key.org, base, files)
    return KnowledgePushed(base=base, chunks=chunks, took_ms=(time.perf_counter() - started) * 1000)


@router.get("/v1/knowledge")
async def bases(key: KeyDep, knowledge: KeptKnowledgeDep) -> KnowledgeList:
    """Every base this org has pushed: its name, its size, when."""
    return KnowledgeList(
        bases=[
            KnowledgeBase(base=one.base, chunks=one.chunks, pushed_at=one.pushed_at.timestamp())
            for one in await knowledge.bases(key.org)
        ]
    )


@router.delete("/v1/knowledge/{base}", status_code=NO_BODY)
async def drop(base: str, key: KeyDep, knowledge: KeptKnowledgeDep) -> None:
    """The base and every chunk of it, gone. 404 when the org never pushed one by that name."""
    if not await knowledge.drop(key.org, base):
        raise HTTPException(status_code=404, detail=NO_SUCH_BASE.format(base=base))
