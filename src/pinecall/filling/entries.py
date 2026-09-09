"""What a fill writes on the call's log: memory.ops for a recall, docs.sources for a retrieval."""

from __future__ import annotations

from collections.abc import Sequence

from pinecall.knowledge.chunking import body_of
from pinecall.types import Chunk, Fact
from pinecall.types.markers import NOT_FILLED, SKIPPED, MarkerName
from pinecall_protocol.defs import DocSource, MemoryFact, MemoryOp
from pinecall_protocol.events import DocsSources, ErrorEvent, MemoryOps


def a_recall(
    contact: str, query: str, facts: Sequence[Fact], took_ms: float, speech_id: str | None
) -> MemoryOps:
    """The memory.ops entry of one turn: op recall, the facts as the model read them, scored."""
    return MemoryOps(
        ops=[
            MemoryOp(
                op="recall",
                contact=contact,
                query=query,
                facts=[
                    MemoryFact(
                        id=fact.id,
                        text=fact.text,
                        category=fact.category,
                        score=fact.score,
                        source=fact.source,
                    )
                    for fact in facts
                ],
                took_ms=took_ms,
            )
        ],
        speech_id=speech_id,
    )


# The excerpt is the text the model read — the body under its heading path — because the
# grounded judge reads `docs.sources` as the evidence a stated price or hour had to come from
# (evals/bridge.py `_excerpts`): what the log does not carry, no judge can find afterwards.
def a_retrieval(
    query: str, chunks: Sequence[Chunk], took_ms: float, speech_id: str | None
) -> DocsSources:
    """The docs.sources entry of one turn: every chunk, where it came from, how well it matched."""
    return DocsSources(
        query=query,
        sources=[
            DocSource(
                id=chunk.id,
                path=chunk.path,
                heading=chunk.heading,
                score=chunk.score,
                excerpt=body_of(chunk.text, chunk.heading),
            )
            for chunk in chunks
        ],
        took_ms=took_ms,
        speech_id=speech_id,
    )


def a_skip(name: MarkerName, why: str) -> ErrorEvent:
    """The error entry of a marker the gateway could not fill: recoverable, and it names why."""
    code = SKIPPED[name]
    return ErrorEvent(
        code=code,
        message=NOT_FILLED.format(what=code.removesuffix("_skipped"), why=why),
        recoverable=True,
    )
