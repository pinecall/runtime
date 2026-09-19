"""A Knowledge answering the chunks it was given, and remembering what it was asked."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pinecall.knowledge import Base
from pinecall.types import Chunk, Env, KnowledgeFile

LEARNED = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)

# The embedder a fake base says wrote its vectors: a listing that left it out was a listing where a
# tenant learned of a mismatch from a 409 instead of from the list.
THE_MODEL = "BAAI/bge-m3"


def a_chunk(id: str, heading: str, text: str, score: float = 1.0) -> Chunk:
    """One chunk of the clinic's base, as search would score it."""
    return Chunk(id=id, base="clinica", path="tarifas.md", heading=heading, text=text, score=score)


@dataclass
class ScriptedKnowledge:
    """A Knowledge answering the chunks it was given, and remembering what it was searched for."""

    answers: list[Chunk] = field(default_factory=list[Chunk])
    failing: Exception | None = None
    searched: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    pushed: dict[str, list[KnowledgeFile]] = field(default_factory=dict[str, list[KnowledgeFile]])

    async def put(
        self,
        org: str,  # noqa: ARG002 — the Protocol's shape
        env: Env,  # noqa: ARG002 — the Protocol's shape
        holder: str | None,  # noqa: ARG002 — the Protocol's shape
        base: str,
        files: Sequence[KnowledgeFile],
    ) -> int:
        if self.failing is not None:
            raise self.failing
        self.pushed[base] = list(files)
        return len(files) * 2

    async def bases(self, org: str, env: Env, holder: str | None = None) -> list[Base]:  # noqa: ARG002
        return [
            Base(base=base, chunks=len(files) * 2, model=THE_MODEL, pushed_at=LEARNED)
            for base, files in sorted(self.pushed.items())
        ]

    async def drop(self, org: str, env: Env, holder: str | None, base: str) -> bool:  # noqa: ARG002
        return self.pushed.pop(base, None) is not None

    async def kept(self, org: str) -> int:  # noqa: ARG002
        """Every base's chunks, on this fake's own cut."""
        return sum(self.how_many_chunks(files) for files in self.pushed.values())

    def how_many_chunks(self, files: Sequence[KnowledgeFile]) -> int:
        """This fake cuts every file into two, so a push of one file is two chunks."""
        return len(files) * 2

    async def search(
        self,
        org: str,
        env: Env,
        holder: str | None,
        base: str,
        query: str,
        *,
        k: int = 8,
        min_score: float | None = None,
    ) -> list[Chunk]:
        if self.failing is not None:
            raise self.failing
        self.searched.append(
            {
                "org": org,
                "env": env,
                "holder": holder,
                "base": base,
                "query": query,
                "k": k,
                "min_score": min_score,
            }
        )
        return list(self.answers)[:k]

    async def whole_texts(
        self,
        org: str,  # noqa: ARG002 — the Protocol's shape
        env: Env,  # noqa: ARG002 — the Protocol's shape
        holder: str | None,  # noqa: ARG002 — the Protocol's shape
        base: str,
    ) -> list[KnowledgeFile]:
        """The files pushed whole under that name, in the order they were pushed."""
        return [file for file in self.pushed.get(base, []) if file.mode == "whole"]
