"""The knowledge and memory doors: bases and their files, a contact's facts, and the goldens."""

import time
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query

from pinecall.domain.agent import DEFAULT_CHUNKS_PER_TURN, KnowledgeFile
from pinecall.domain.errors import DeclarationRefused, NotFound
from pinecall.domain.scope import Scope
from pinecall.gateway._call_setup import keys_of, tuned
from pinecall.gateway._deps import (
    GatewayDep,
    KnowledgeKey,
    MemoryKey,
    ScopeDep,
    TeamKey,
    asked_by,
    check_paced,
    embedder_of,
)
from pinecall.gateway._gateway import Gateway
from pinecall.gateway._sockets import NO_AGENT
from pinecall.process.recordings import recordings_of
from pinecall.providers import catalog
from pinecall.providers.credentials import thinking
from pinecall.retrieval import extraction, knowledge, memory
from pinecall.retrieval.knowledge import Answered, Push, Question, SearchQuery
from pinecall.tenancy import admission, erasure, reads, scopes
from pinecall.tenancy.reads import Read
from pinecall.wire.rest.calls import Erasure
from pinecall.wire.rest.retrieval import (
    AgentFact,
    AgentMemory,
    ContactFact,
    ContactMemory,
    ExtractionCases,
    ExtractionRun,
    Forgotten,
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
    MemoryGolden,
    MemoryScore,
    OrgFact,
    OrgMemory,
)

router = APIRouter()


# 404, not 204: dropping a mistyped name must not look like success.
NO_SUCH_BASE = "no knowledge base named {base}: nothing was pushed under that name"


NO_SUCH_FILE = "no file {path} in the base {base}"


# One sentence for a fact missing, already ended, or another org's: nothing is told apart.
NO_SUCH_FACT = "no current fact {id} in this key's org and world"


# A 400, not an empty pass: a green run for a feature the agent lacks would mislead.
KEEPS_NOTHING = "agent {slug} declares no extraction.remember: there is nothing to extract"


# Each run is up to fifty model calls: a suite runs once, a loop is stopped.
RUNS_A_MINUTE = 6


TOO_MANY_RUNS = "more than six extraction runs this minute from this org: try again in a minute"


# The whole history, superseded facts included; a call reads the current ones through `recall`.
@router.get("/v1/contacts/{contact}/memory")
async def contact_memory(
    contact: str, key: MemoryKey, where: ScopeDep, box: GatewayDep
) -> ContactMemory:
    """Every fact ever kept of the contact, current first."""
    await reads.record(box.connections.pool, where, Read(contact, "memory", asked_by(key)))
    return ContactMemory(
        facts=[
            ContactFact(
                id=fact.id,
                text=fact.text,
                category=fact.category,
                source=fact.taught_by,
                valid_from=fact.valid_from.timestamp(),
                invalidated_at=None
                if fact.invalidated_at is None
                else fact.invalidated_at.timestamp(),
            )
            for fact in await memory.history(box.connections.pool, where, contact)
        ]
    )


# Reading and erasing are the contact's rights: no quota ever refuses them.
@router.delete("/v1/contacts/{contact}/memory")
async def forget_contact(
    contact: str, _key: MemoryKey, where: ScopeDep, box: GatewayDep
) -> Forgotten:
    """Every fact of the contact deleted, history included; zero is an answer, not a 404."""
    return Forgotten(forgotten=await memory.forget(box.connections.pool, where, contact))


# A person's "delete my data": what they said on every call of the world, and what was kept.
@router.delete("/v1/contacts/{contact}")
async def erase_contact(contact: str, key: TeamKey, where: ScopeDep, box: GatewayDep) -> Erasure:
    """Erase a contact in the world: every call they were on and every fact kept of them."""
    recordings = recordings_of(box.connections.settings, box.connections.http)
    erased = await erasure.contact(
        box.connections.pool, recordings, where, contact, by=asked_by(key)
    )
    for call in erased.calls:
        box.logs.forget(call)
    return erased.trail


@router.post("/v1/contacts/memory/eval")
async def memory_eval(
    body: MemoryGolden, _key: MemoryKey, where: ScopeDep, box: GatewayDep
) -> MemoryScore:
    """A memory golden asked of the ranking a call reads, on facts it writes and forgets."""
    return await memory.ask_golden(
        box.connections.pool, embedder_of(box), where, body, at=_now(box)
    )


@router.get("/v1/agents/{slug}/memory")
async def agent_memory(
    slug: str,
    key: MemoryKey,
    where: ScopeDep,
    box: GatewayDep,
    page: Annotated[memory.Paging, Query()],
) -> AgentMemory:
    """The current facts the agent's calls taught, across contacts, newest first, a page."""
    await reads.record(box.connections.pool, where, Read(slug, "memory", asked_by(key)))
    found = await memory.taught_by(box.connections.pool, where, slug, page=page)
    return AgentMemory(
        facts=[
            AgentFact(
                id=fact.id,
                contact=fact.contact,
                text=fact.text,
                category=fact.category,
                written_at=fact.valid_from.timestamp(),
            )
            for fact in found.facts
        ],
        next=found.next,
    )


@router.get("/v1/memory")
async def org_memory(
    key: MemoryKey, where: ScopeDep, box: GatewayDep, page: Annotated[memory.Paging, Query()]
) -> OrgMemory:
    """The current facts every agent's calls taught, each with its agent, newest first, a page."""
    await reads.record(box.connections.pool, where, Read(where.org, "memory", asked_by(key)))
    found = await memory.taught_by(box.connections.pool, where, None, page=page)
    return OrgMemory(
        facts=[
            OrgFact(
                id=fact.id,
                agent=found.agents[fact.id],
                contact=fact.contact,
                text=fact.text,
                category=fact.category,
                written_at=fact.valid_from.timestamp(),
            )
            for fact in found.facts
        ],
        next=found.next,
    )


# The row stays, ended: the history still shows it. Erasing a contact is the door above.
@router.delete("/v1/memory/facts/{id}")
async def forget_fact(
    fact: Annotated[UUID, Path(alias="id")], _key: MemoryKey, where: ScopeDep, box: GatewayDep
) -> Forgotten:
    """One current fact ended from now on; 404 when there is none by that id."""
    if not await memory.invalidated(box.connections.pool, where, str(fact), at=_now(box)):
        raise NotFound(NO_SUCH_FACT.format(id=fact))
    return Forgotten(forgotten=1)


# Here and not in the CLI: the agent's model, the org's keys and the tuned policy live here.
@router.post("/v1/agents/{slug}/memory/extraction")
async def memory_extraction(
    slug: str, body: ExtractionCases, _key: MemoryKey, where: ScopeDep, box: GatewayDep
) -> ExtractionRun:
    """One hang-up per case on the agent's own model and keys, each answer judged by code."""
    await check_paced(box, f"{where.org} memory/extraction", RUNS_A_MINUTE, TOO_MANY_RUNS)
    registration = box.sockets.of(where, slug)
    if registration is None or registration.scope.org != where.org:
        raise NotFound(NO_AGENT.format(slug=slug))
    configured = await catalog.providers(box.connections.pool)
    config, _ = await tuned(box.connections.pool, registration.config, where, configured)
    policy = config.memory
    if policy is None or not policy.remember:
        raise DeclarationRefused(KEEPS_NOTHING.format(slug=slug))
    for case in body.cases:
        extraction.check_case(case, policy)
    model = thinking(
        config, configured, await keys_of(box.connections.pool, box.connections.vault, where)
    )
    tools = tuple(tool.name for tool in config.tools)
    started = time.perf_counter()
    # One after the other: side by side would measure the vendor's concurrency.
    results = [
        await extraction.extract_case(model, case, policy=policy, tools=tools, at=_now(box))
        for case in body.cases
    ]
    return ExtractionRun(
        agent=slug,
        model=model.vendor if model.model is None else f"{model.vendor}/{model.model}",
        cases=len(results),
        held=sum(item.held for item in results),
        took_ms=(time.perf_counter() - started) * 1000,
        results=results,
    )


async def _knowledge_corner(_key: KnowledgeKey, where: ScopeDep) -> Scope:
    return where


# The corner of a key that opens knowledge: the org and world from the key, the holder its own.
KnowledgeCorner = Annotated[Scope, Depends(_knowledge_corner)]


@router.put("/v1/knowledge/{base}")
async def push(
    base: str, body: KnowledgePush, where: KnowledgeCorner, box: GatewayDep
) -> KnowledgePushed:
    """Replace the base with the folder: cut, embedded where it changed, indexed."""
    started = time.perf_counter()
    embedder = embedder_of(box)
    files = tuple(knowledge.cut(KnowledgeFile(item.path, item.text)) for item in body.files)
    folder = Push(base=base, files=files, at=box.logs.store.clock())
    await _admitted(box, where, folder, path=None)
    await knowledge.put(box.connections.pool, embedder, where, folder)
    took_ms = (time.perf_counter() - started) * 1000
    return KnowledgePushed(base=base, chunks=folder.chunks, took_ms=took_ms)


@router.get("/v1/knowledge")
async def bases(where: KnowledgeCorner, box: GatewayDep) -> KnowledgeList:
    """Every base the key's corner reads, its size, its model and when it was pushed."""
    return KnowledgeList(
        bases=[
            KnowledgeBase(
                base=item.base, chunks=item.chunks, model=item.model, pushed_at=item.pushed_at
            )
            for item in await knowledge.bases(box.connections.pool, where)
        ]
    )


# Before `/{base}`, so "attached" is never read as a base's name.
@router.get("/v1/knowledge/attached")
async def attached(where: KnowledgeCorner, box: GatewayDep) -> KnowledgeUses:
    """Each base some agent attaches, and the agents whose settings in the corner attach it."""
    readers: dict[str, list[str]] = {}
    for agent, tuning in sorted((await scopes.every_tuning(box.connections.pool, where)).items()):
        for docs in tuning.bases or ():
            readers.setdefault(docs.base, []).append(agent)
    return KnowledgeUses(
        bases=[KnowledgeUse(base=base, agents=agents) for base, agents in sorted(readers.items())]
    )


@router.get("/v1/knowledge/{base}")
async def files(base: str, where: KnowledgeCorner, box: GatewayDep) -> KnowledgeFiles:
    """The base's files, each with its size and chunks, never its text."""
    listed = await knowledge.files(box.connections.pool, where, base)
    if listed is None:
        raise NotFound(NO_SUCH_BASE.format(base=base))
    return KnowledgeFiles(
        base=base,
        kept=bool(listed),
        files=[
            KnowledgeFileRow(
                path=item.path, chars=item.chars, chunks=item.chunks, pushed_at=item.pushed_at
            )
            for item in listed
        ],
    )


@router.get("/v1/knowledge/{base}/files/{path:path}")
async def read_file(
    base: str, path: str, where: KnowledgeCorner, box: GatewayDep
) -> KnowledgeFileRead:
    """One file of the base, text and all."""
    found = await knowledge.file(box.connections.pool, where, base, path)
    if found is None:
        raise NotFound(NO_SUCH_FILE.format(base=base, path=path))
    return KnowledgeFileRead(
        path=found.path, text=found.text or "", chunks=found.chunks, pushed_at=found.pushed_at
    )


@router.put("/v1/knowledge/{base}/files/{path:path}")
async def put_file(
    base: str, path: str, body: KnowledgeFilePut, where: KnowledgeCorner, box: GatewayDep
) -> KnowledgeFilePushed:
    """Put one file into the base, beginning the base when there is none; only it is cut."""
    started = time.perf_counter()
    embedder = embedder_of(box)
    cut = knowledge.cut(KnowledgeFile(path, body.text))
    folder = Push(base=base, files=(cut,), at=box.logs.store.clock())
    await _admitted(box, where, folder, path=path)
    await knowledge.put_file(box.connections.pool, embedder, where, folder)
    took_ms = (time.perf_counter() - started) * 1000
    return KnowledgeFilePushed(base=base, path=path, chunks=folder.chunks, took_ms=took_ms)


@router.delete("/v1/knowledge/{base}/files/{path:path}", status_code=204)
async def drop_file(base: str, path: str, where: KnowledgeCorner, box: GatewayDep) -> None:
    """Take one file and its chunks out of the base, and the base with its last file."""
    if not await knowledge.drop_file(box.connections.pool, where, base, path):
        raise NotFound(NO_SUCH_FILE.format(base=base, path=path))


@router.delete("/v1/knowledge/{base}", status_code=204)
async def drop(base: str, where: KnowledgeCorner, box: GatewayDep) -> None:
    """Drop the holder's own copy of the base and its chunks."""
    if not await knowledge.drop(box.connections.pool, where, base):
        raise NotFound(NO_SUCH_BASE.format(base=base))


# Sequential: searches at once would measure the pool, not the index. No floor: the golden
# measures the ranking, not an attachment's threshold.
@router.post("/v1/knowledge/{base}/eval")
async def evaluate(
    base: str, body: KnowledgeGolden, where: KnowledgeCorner, box: GatewayDep
) -> KnowledgeScore:
    """Ask the golden's questions of the base and score recall@k and nDCG@10, with no model."""
    embedder = embedder_of(box)
    found = next(
        (item for item in await knowledge.bases(box.connections.pool, where) if item.base == base),
        None,
    )
    if found is None:
        raise NotFound(NO_SUCH_BASE.format(base=base))
    k = body.k or DEFAULT_CHUNKS_PER_TURN
    started = time.perf_counter()
    answered: list[Answered] = []
    for item in body.questions:
        query = SearchQuery(query=item.asks, k=k, bases={base: 0.0})
        searched = await knowledge.search(box.connections.pool, embedder, where, query)
        answered.append(Answered(Question(item.asks, item.expects), searched.found))
    score = knowledge.score_docs(answered, k)
    return KnowledgeScore(
        base=base,
        model=found.model,
        questions=score.questions,
        k=score.k,
        recall_at_k=score.figures.recall_at_k,
        ndcg_at_10=score.figures.ndcg_at_10,
        took_ms=(time.perf_counter() - started) * 1000,
        misses=[
            GoldenMiss(
                asks=miss.question.asks,
                expects=miss.question.expects,
                found=[knowledge.where(chunk) for chunk in miss.found],
            )
            for miss in score.misses
        ],
    )


# A push is sized whole before a row is written: what the org keeps in this world, less the
# copy it replaces, plus what it was cut into.
async def _admitted(box: Gateway, where: Scope, push: Push, *, path: str | None) -> None:
    kept = await knowledge.kept(box.connections.pool, where.org, where.env)
    replaced = await knowledge.chunks_kept(box.connections.pool, where, push.base, path=path)
    keeping = kept - replaced + push.chunks
    await admission.admit_push(box.connections.pool, where.org, where.env, keeping=keeping)


def _now(box: Gateway) -> datetime:
    return datetime.fromtimestamp(box.logs.store.clock(), UTC)
