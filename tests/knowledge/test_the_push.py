"""A push hands the embedder one document per FILE, and writes back what it answered, in order."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from pinecall.knowledge import PgKnowledge
from pinecall.providers.embedder import DIMENSIONS
from pinecall.types import PRODUCTION
from tests.knowledge.files import CLINICA, TARIFAS

pytestmark = pytest.mark.unit


class RecordingEmbedder:
    """An embedder that answers a vector per chunk and keeps the documents it was handed."""

    dimensions = DIMENSIONS

    def __init__(self) -> None:
        self.documents: list[list[str]] = []

    async def model(self) -> str:
        return "pplx-embed-context-v1-0.6b"

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[0.5] * DIMENSIONS for _text in texts]

    async def embed_documents(self, documents: Sequence[Sequence[str]]) -> list[list[list[float]]]:
        self.documents = [list(chunks) for chunks in documents]
        return [[[float(len(chunk))] * DIMENSIONS for chunk in chunks] for chunks in documents]


class RecordingPool:
    """A pool that runs nothing and keeps the one statement a push sends and its arguments."""

    def __init__(self) -> None:
        self.arguments: tuple[Any, ...] = ()

    async def execute(self, query: str, /, *args: Any) -> str:
        assert "knowledge_chunks" in query
        self.arguments = args
        return "INSERT 0 0"

    async def fetch(self, query: str, /, *args: Any) -> Sequence[Mapping[str, Any]]:
        raise NotImplementedError(query or args)

    async def fetchrow(self, query: str, /, *args: Any) -> Mapping[str, Any] | None:
        raise NotImplementedError(query or args)

    async def close(self) -> None:
        return None


async def test_a_file_is_one_document_so_a_chunk_is_embedded_seeing_its_neighbours() -> None:
    """The whole point of the contextual model: the chunks of one file go out together."""
    embedder = RecordingEmbedder()
    pool = RecordingPool()
    assert (
        await PgKnowledge(pool, embedder).put("org", PRODUCTION, "clinica", [CLINICA, TARIFAS]) == 4
    )
    assert [len(document) for document in embedder.documents] == [2, 2]
    assert embedder.documents[0][0].startswith("Clínica Norte › Horarios")
    assert embedder.documents[1][0].startswith("Tarifas › Revisión")


async def test_the_vectors_are_written_back_flat_in_the_order_the_files_were_cut() -> None:
    """One list per document out, one row per chunk in: a reordering here loses every vector."""
    embedder = RecordingEmbedder()
    pool = RecordingPool()
    await PgKnowledge(pool, embedder).put("org", PRODUCTION, "clinica", [CLINICA, TARIFAS])
    paths, _headings, _ordinals, texts, vectors = pool.arguments[6:]
    assert paths == ["clinica.md", "clinica.md", "tarifas.md", "tarifas.md"]
    assert [vector.split(",")[0].lstrip("[") for vector in vectors] == [
        str(float(len(text))) for text in texts
    ]


async def test_the_bases_row_keeps_the_model_that_answered_and_its_width() -> None:
    pool = RecordingPool()
    await PgKnowledge(pool, RecordingEmbedder()).put("org", PRODUCTION, "clinica", [CLINICA])
    assert pool.arguments[3:6] == ("pplx-embed-context-v1-0.6b", DIMENSIONS, 2)
