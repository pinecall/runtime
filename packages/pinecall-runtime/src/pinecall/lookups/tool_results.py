"""What recall and search answer with: the JSON object the model reads inside a tool result."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pinecall.knowledge.chunking import body_of
from pinecall.types import Chunk, Fact
from pinecall_protocol.rest import FoundChunk, SearchFound


# The source and the date are not decoration: Anthropic asks that the nature and origin of
# anything from outside the conversation be explicit, so the model can weigh it, and a fact that
# says which call it came from is a fact the model can weigh.
# docs/security/prompt-injection.md, "Memory and retrieval are tools".
def recall_result(facts: Sequence[Fact]) -> dict[str, Any]:
    """`{"facts": [{text, source, since}]}` — nothing else, in the order recall scored them."""
    return {
        "facts": [
            {
                "text": " ".join(fact.text.split()),
                "source": fact.source,
                "since": fact.valid_from.date().isoformat(),
            }
            for fact in facts
            if fact.text.strip()
        ]
    }


# The heading travels in its own field, so the body alone goes under `text`: the indexes read the
# heading path over the body, and the model would otherwise read it twice. The shape is the wire's
# (SearchFound), because the app reads it back through `this.knowledge.search`.
def found(chunks: Sequence[Chunk]) -> dict[str, Any]:
    """`{"chunks": [{path, heading, text}]}` — nothing else, best match first."""
    return SearchFound(
        chunks=[
            FoundChunk(
                path=chunk.path, heading=chunk.heading, text=body_of(chunk.text, chunk.heading)
            )
            for chunk in chunks
        ]
    ).model_dump(mode="json")
