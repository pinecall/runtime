"""A fact another embedder wrote is written again from its own text, and nothing else moves."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from typing import Any

import pytest

from pinecall.memory.reembedding_postgres import reembed
from pinecall.providers.embedder import DIMENSIONS
from tests.pools import Held, acquired

pytestmark = pytest.mark.unit

NEW = "pplx-embed-context-v1-4b"


class Embedder:
    """Answers one vector per text and keeps every batch it was handed."""

    dimensions = DIMENSIONS

    def __init__(self) -> None:
        self.batches: list[list[str]] = []

    async def model(self) -> str:
        return NEW

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.batches.append(list(texts))
        return [[1.0] + [0.0] * (DIMENSIONS - 1) for _ in texts]

    async def embed_documents(self, documents: Sequence[Sequence[str]]) -> list[list[list[float]]]:
        raise NotImplementedError(documents)


class Pool:
    """The facts another model wrote, and every UPDATE sent for them."""

    def __init__(self, stale: list[Mapping[str, Any]]) -> None:
        self.stale = stale
        self.asked: tuple[Any, ...] = ()
        self.writes: list[tuple[Any, ...]] = []

    async def fetch(self, query: str, /, *args: Any) -> Sequence[Mapping[str, Any]]:
        assert "model <> $1" in query
        self.asked = args
        return self.stale

    async def execute(self, query: str, /, *args: Any) -> str:
        assert query.strip().startswith("UPDATE contact_memories SET embedding")
        self.writes.append(args)
        return "UPDATE 1"

    async def fetchrow(self, query: str, /, *args: Any) -> Mapping[str, Any] | None:
        raise NotImplementedError(query or args)

    def acquire(self) -> AbstractAsyncContextManager[Held]:
        return acquired(self)

    async def close(self) -> None:
        return None


async def test_only_stale_facts_are_asked_for_and_each_is_written_with_this_model() -> None:
    pool = Pool(
        [{"id": "a", "text": "Alérgica a la penicilina."}, {"id": "b", "text": "Prefiere mañanas."}]
    )
    embedder = Embedder()
    assert await reembed(pool, embedder) == 2
    assert pool.asked == (NEW,)
    assert embedder.batches == [["Alérgica a la penicilina.", "Prefiere mañanas."]]
    assert [(write[0], write[2]) for write in pool.writes] == [("a", NEW), ("b", NEW)]
    assert pool.writes[0][1].startswith("[1")


async def test_the_facts_go_to_the_embedder_in_batches() -> None:
    pool = Pool([{"id": str(n), "text": f"hecho {n}"} for n in range(5)])
    embedder = Embedder()
    assert await reembed(pool, embedder, batch=2) == 5
    assert [len(batch) for batch in embedder.batches] == [2, 2, 1]


async def test_nothing_stale_asks_the_embedder_nothing() -> None:
    embedder = Embedder()
    assert await reembed(Pool([]), embedder) == 0
    assert embedder.batches == []
