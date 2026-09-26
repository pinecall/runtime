"""The knowledge doors: a push, the list, a drop, the golden, who reads, the files one by one."""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException
from starlette.status import HTTP_204_NO_CONTENT

from pinecall.api.deps import AdmissionDep, KeptKnowledgeDep, KnowledgeKeyDep, TuningDep
from pinecall.auth.keys import held_by
from pinecall.knowledge.scoring import Answered, Question, Score, scored
from pinecall.types import KnowledgeFile
from pinecall.types.knowledge import DEFAULT_CHUNKS_PER_TURN
from pinecall_protocol.rest import (
    GoldenMiss,
    KnowledgeBase,
    KnowledgeFilePushed,
    KnowledgeFilePut,
    KnowledgeFileRead,
    KnowledgeFileRow,
    KnowledgeFiles,
    KnowledgeGolden,
    KnowledgeList,
    KnowledgePush,
    KnowledgePushed,
    KnowledgeScore,
    KnowledgeUse,
    KnowledgeUses,
)

router = APIRouter()

# Dropping a name nobody pushed is a typo, and a verb that answered yes to it would send the
# tenant looking for the change somewhere else. 404, and the org is never named: it is the key's.
NO_SUCH_BASE = "no knowledge base named {base}: nothing was pushed under that name"

# A file nobody put under that path. A base pushed before its files were kept (0041) lists none
# and says `kept: false`, and the way out is a push again, or a file put into it.
NO_SUCH_FILE = "no file {path} in the base {base}"


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
    # What the org would keep once this push has landed: everything it holds in both worlds, less
    # what the base being replaced frees — the push replaces it whole — plus what these files
    # become. Judged before a row is written, because a push is one statement and all or nothing.
    held = await knowledge.bases(key.org, key.env, held_by(key))
    freed = next((one.chunks for one in held if one.base == base), 0)
    keeping = await knowledge.kept(key.org) - freed + knowledge.how_many_chunks(files)
    await admission.a_push(key.org, keeping)
    chunks = await knowledge.put(key.org, key.env, held_by(key), base, files)
    return KnowledgePushed(base=base, chunks=chunks, took_ms=(time.perf_counter() - started) * 1000)


@router.get("/v1/knowledge")
async def bases(key: KnowledgeKeyDep, knowledge: KeptKnowledgeDep) -> KnowledgeList:
    """Every base this org has pushed in the key's world: its name, its size, when."""
    return KnowledgeList(
        bases=[
            KnowledgeBase(
                base=one.base,
                chunks=one.chunks,
                model=one.model,
                pushed_at=one.pushed_at.timestamp(),
            )
            for one in await knowledge.bases(key.org, key.env, held_by(key))
        ]
    )


# Which agents read each base: off every agent's newest settings in the key's world, the
# corner's own else the org's — the same rows a session is built from. Registered before the
# `{base}` doors so "attached" is never taken for a base's name.
@router.get("/v1/knowledge/attached")
async def attached(key: KnowledgeKeyDep, kept: TuningDep) -> KnowledgeUses:
    """One row per base any agent's settings attach, with the agents that read it."""
    readers: dict[str, list[str]] = {}
    for slug, row in (await kept.every_newest(key.org, key.env, held_by(key))).items():
        for docs in row.value.bases or ():
            readers.setdefault(docs.base, []).append(slug)
    return KnowledgeUses(
        bases=[KnowledgeUse(base=base, agents=agents) for base, agents in sorted(readers.items())]
    )


# The files of a base, one at a time — what a person at the console reads, adds, edits and takes
# out, with no folder on a laptop and no push of the whole. The base stays the unit a push
# replaces and a drop forgets; a file put alone is re-cut into its own chunks and nothing else's.
# Read after `attached`, so that word is never taken for a base's name.
@router.get("/v1/knowledge/{base}")
async def files(base: str, key: KnowledgeKeyDep, knowledge: KeptKnowledgeDep) -> KnowledgeFiles:
    """Every file of the base, by path, with its size and what it became; never the text."""
    held = await knowledge.bases(key.org, key.env, held_by(key))
    if not any(one.base == base for one in held):
        raise HTTPException(status_code=404, detail=NO_SUCH_BASE.format(base=base))
    listed = await knowledge.files(key.org, key.env, held_by(key), base)
    return KnowledgeFiles(
        base=base,
        kept=bool(listed),
        files=[
            KnowledgeFileRow(
                path=one.path,
                chars=one.chars,
                chunks=one.chunks,
                pushed_at=one.pushed_at.timestamp(),
            )
            for one in listed
        ],
    )


# `{file:path}`: a file's path has slashes in it — `faq/horarios.md` — so the converter is the
# path one; the name is the file's, and the console's own catch-all keeps `{path:path}` to itself.
@router.get("/v1/knowledge/{base}/files/{file:path}")
async def read_file(
    base: str, file: str, key: KnowledgeKeyDep, knowledge: KeptKnowledgeDep
) -> KnowledgeFileRead:
    """One file of the base, text and all."""
    found = await knowledge.file(key.org, key.env, held_by(key), base, file)
    if found is None:
        raise HTTPException(status_code=404, detail=_no_such_file(base, file))
    return KnowledgeFileRead(
        path=found.path,
        text=found.text or "",
        chunks=found.chunks,
        pushed_at=found.pushed_at.timestamp(),
    )


@router.put("/v1/knowledge/{base}/files/{file:path}")
async def put_file(
    base: str,
    file: str,
    said: KnowledgeFilePut,
    key: KnowledgeKeyDep,
    knowledge: KeptKnowledgeDep,
    admission: AdmissionDep,
) -> KnowledgeFilePushed:
    """The file put into the base, new or replaced in place, and re-cut alone; how many chunks."""
    started = time.perf_counter()
    wanted = KnowledgeFile(path=file, text=said.text)
    # Judged as a push is: what the org would keep once this file has landed, less what this
    # corner's own copy of it frees, plus what the text becomes.
    freed = await knowledge.freed_by(key.org, key.env, held_by(key), base, file)
    keeping = await knowledge.kept(key.org) - freed + knowledge.how_many_chunks([wanted])
    await admission.a_push(key.org, keeping)
    chunks = await knowledge.put_file(key.org, key.env, held_by(key), base, wanted)
    return KnowledgeFilePushed(
        base=base, path=file, chunks=chunks, took_ms=(time.perf_counter() - started) * 1000
    )


@router.delete("/v1/knowledge/{base}/files/{file:path}", status_code=HTTP_204_NO_CONTENT)
async def drop_file(
    base: str, file: str, key: KnowledgeKeyDep, knowledge: KeptKnowledgeDep
) -> None:
    """The file and its chunks gone; the base too when it was the last. 404 when there is none."""
    if not await knowledge.drop_file(key.org, key.env, held_by(key), base, file):
        raise HTTPException(status_code=404, detail=_no_such_file(base, file))


def _no_such_file(base: str, path: str) -> str:
    return NO_SUCH_FILE.format(base=base, path=path)


@router.delete("/v1/knowledge/{base}", status_code=HTTP_204_NO_CONTENT)
async def drop(base: str, key: KnowledgeKeyDep, knowledge: KeptKnowledgeDep) -> None:
    """The base and every chunk of it, gone. 404 when the org never pushed one by that name."""
    if not await knowledge.drop(key.org, key.env, held_by(key), base):
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
    bases_held = await knowledge.bases(key.org, key.env, held_by(key))
    held = next((one for one in bases_held if one.base == base), None)
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
            chunks=await knowledge.search(key.org, key.env, held_by(key), [base], one.asks, k=k),
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
