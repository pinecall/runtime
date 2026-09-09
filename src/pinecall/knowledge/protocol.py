"""Knowledge, the Protocol: a tenant's bases pushed whole, listed, dropped, searched two ways."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from pinecall.knowledge.store import Base
from pinecall.types import Chunk, KnowledgeFile
from pinecall.types.knowledge import DEFAULT_CHUNKS_PER_TURN


# What the doors and the fill hold: the verbs, never the table. PgKnowledge is the one
# implementation in the tree; a test hands the service a fake of this shape and no Postgres.
class Knowledge(Protocol):
    """The knowledge base as a turn and a push see it: put, bases, drop, search."""

    async def put(self, org: str, base: str, files: Sequence[KnowledgeFile]) -> int:
        """Replace the base with these files, chunked and embedded; how many chunks it became."""
        ...

    async def bases(self, org: str) -> list[Base]:
        """Every base this org pushed, by name."""
        ...

    async def drop(self, org: str, base: str) -> bool:
        """Forget the base and its chunks. False when the org never pushed one by that name."""
        ...

    async def search(
        self,
        org: str,
        base: str,
        query: str,
        *,
        k: int = DEFAULT_CHUNKS_PER_TURN,
        min_score: float | None = None,
    ) -> list[Chunk]:
        """The best k chunks for the query, by meaning and by words, fused; under min_score, cut."""
        ...
