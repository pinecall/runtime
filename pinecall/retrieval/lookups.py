"""A call's lookups: recall and search for one turn, on its log; its turns read for memory."""

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import psycopg

from pinecall.domain.agent import AgentConfig
from pinecall.domain.call import CallContext
from pinecall.domain.errors import EmbedderUnreachable, WrongModel
from pinecall.domain.names import JsonObject
from pinecall.domain.org import Quotas
from pinecall.domain.scope import Scope
from pinecall.log.logs import Log
from pinecall.postgres.pool import Pool
from pinecall.retrieval import knowledge, memory
from pinecall.retrieval.embed import Embedder
from pinecall.retrieval.extraction import Heard, Spoken
from pinecall.retrieval.knowledge import SearchQuery
from pinecall.retrieval.memory import Recall, RecalledFact
from pinecall.wire.events import AgentTurnEnded, DocsSources, ErrorEvent, MemoryOps, UserTurnEnded
from pinecall.wire.frames import Entry
from pinecall.wire.parts import DocSource, MemoryFact, MemoryOp, PlatformTool
from pinecall.wire.rest.calls import LookupRequest
from pinecall.wire.rest.retrieval import FoundChunk, SearchFound

SKIPPED = "{tool} did not run for this turn: {why}"


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OnTheCall:
    """The call a lookup runs for: whose it is, how it arrived, what its agent declared, its log."""

    scope: Scope
    context: CallContext
    config: AgentConfig
    log: Log
    # When the door answered: recency is measured from it.
    now: datetime


# The results reach the model inside a tool result and nowhere else. A lookup that cannot run is
# an entry and the empty shape: the turn never waits on it. Nothing is written when a tool is
# off: an empty memory.ops would say a recall ran, and an empty docs.sources reads to the
# grounded judge as a search that found nothing.
async def lookup(
    pool: Pool,
    embedder: Embedder | None,
    call: OnTheCall,
    request: LookupRequest,
    *,
    quotas: Quotas,
) -> JsonObject:
    """Recall or search for the call, written on its log; the empty shape when nothing may run."""
    if embedder is None or not _switched_on(call, request.tool, quotas):
        return _nothing_found(request.tool)
    started = time.perf_counter()
    try:
        if request.tool == "recall":
            return await _recalled(pool, embedder, call, request, started=started)
        return await _searched(pool, embedder, call, request, started=started)
    except (EmbedderUnreachable, WrongModel, psycopg.Error) as failed:
        logger.warning("%s did not run for call %s", request.tool, call.context.call, exc_info=True)
        why = SKIPPED.format(tool=request.tool, why=str(failed) or type(failed).__name__)
        skipped = ErrorEvent(code=f"{request.tool}_skipped", message=why, recoverable=True)
        await call.log.append("error", skipped.written())
        return _nothing_found(request.tool)


def heard_in(
    context: CallContext, config: AgentConfig, entries: Sequence[Entry], *, at: datetime
) -> Heard | None:
    """What the extraction model reads of a finished call; None when nothing is to be kept."""
    contact = context.remembered_as
    if contact is None or config.memory is None:
        return None
    return Heard(
        contact=contact,
        call=context.call,
        channel=context.channel,
        policy=config.memory,
        tools=tuple(tool.name for tool in config.tools),
        spoken=[spoken for entry in entries if (spoken := _spoken(entry)) is not None],
        at=at,
    )


# A memory at its cap is still read, since a cap is about keeping.
def _switched_on(call: OnTheCall, tool: PlatformTool, quotas: Quotas) -> bool:
    if tool == "recall":
        keeps = call.config.memory is not None and call.context.remembered_as is not None
        return keeps and not quotas.switched_off("memory_facts")
    return bool(call.config.bases) and not quotas.switched_off("knowledge_chunks")


# The contact is the one the platform knows, never one the model named.
async def _recalled(
    pool: Pool, embedder: Embedder, call: OnTheCall, request: LookupRequest, *, started: float
) -> JsonObject:
    contact = call.context.remembered_as or ""
    query = _query(request)
    wanted = Recall(contact=contact, query=query, at=call.now)
    recalled = await memory.recall(pool, embedder, call.scope, wanted)
    op = MemoryOp(
        op="recall",
        contact=contact,
        query=query,
        facts=[_fact(item) for item in recalled.facts],
        took_ms=_since(started),
    )
    await call.log.append("memory.ops", MemoryOps(ops=[op], speech_id=request.speech_id).written())
    return {
        "facts": [
            {
                "text": " ".join(item.fact.text.split()),
                "source": item.fact.taught_by,
                "since": item.fact.valid_from.date().isoformat(),
            }
            for item in recalled.facts
            if item.fact.text.strip()
        ]
    }


# Every base in one search, so their scores are comparable; the app may say how many it wants.
async def _searched(
    pool: Pool, embedder: Embedder, call: OnTheCall, request: LookupRequest, *, started: float
) -> JsonObject:
    bases = call.config.bases
    query = _query(request)
    wanted = request.input.get("k")
    k = wanted if isinstance(wanted, int) and wanted > 0 else max(docs.k for docs in bases)
    floors = {docs.base: docs.min_score or 0.0 for docs in bases}
    searched = await knowledge.search(
        pool, embedder, call.scope, SearchQuery(query=query, k=k, bases=floors)
    )
    sources = DocsSources(
        query=query,
        sources=[
            DocSource(
                id=item.id,
                base=item.base,
                path=item.path,
                heading=item.heading,
                score=item.score,
                excerpt=item.text,
            )
            for item in searched.found
        ],
        took_ms=_since(started),
        speech_id=request.speech_id,
    )
    await call.log.append("docs.sources", sources.written())
    found = [
        FoundChunk(path=item.path, heading=item.heading, text=item.text) for item in searched.found
    ]
    return SearchFound(chunks=found).written()


def _nothing_found(tool: PlatformTool) -> JsonObject:
    if tool == "recall":
        return {"facts": []}
    return SearchFound(chunks=[]).written()


def _query(request: LookupRequest) -> str:
    return str(request.input.get("query") or "")


def _fact(item: RecalledFact) -> MemoryFact:
    fact = item.fact
    return MemoryFact(
        id=fact.id, text=fact.text, category=fact.category, score=item.score, source=fact.taught_by
    )


def _spoken(entry: Entry) -> Spoken | None:
    if entry.type == "turn.user":
        return Spoken(role="user", text=UserTurnEnded.model_validate(entry.data).text)
    if entry.type == "turn.agent":
        return Spoken(role="agent", text=AgentTurnEnded.model_validate(entry.data).text)
    return None


def _since(started: float) -> float:
    return (time.perf_counter() - started) * 1000
