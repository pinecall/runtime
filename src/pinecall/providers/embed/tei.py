"""TEI, the embedder the box runs: bge-m3 behind Hugging Face's text-embeddings-inference."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx

from pinecall.providers.embedder import DIMENSIONS, WrongWidth

# TEI's own name for a model it was not told, so a refusal always has a word to say.
UNNAMED = "the embedder at TEI_URL"


# One per process, over the one http client the process opened. /info names the model and is
# read once; the width is measured on the vectors themselves, because /info does not carry it —
# a model of another width is refused at the first embed, by name, before a row is written.
class TeiEmbedder:
    """POST /embed in batches, truncating a long text instead of refusing the batch."""

    def __init__(self, url: str, http: httpx.AsyncClient) -> None:
        self._url = url.rstrip("/")
        self._http = http
        self._model: str | None = None

    @property
    def dimensions(self) -> int:
        """bge-m3's width; the first embed proves the model behind the URL answers at it."""
        return DIMENSIONS

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """One vector per text, from TEI, held to the width the tables are declared at."""
        if not texts:
            return []
        model = await self._the_model()
        answer = await self._http.post(
            f"{self._url}/embed", json={"inputs": list(texts), "truncate": True}
        )
        answer.raise_for_status()
        vectors: list[list[float]] = answer.json()
        if vectors and len(vectors[0]) != DIMENSIONS:
            raise WrongWidth(
                f"{model} answers {len(vectors[0])}-wide vectors; the tables are declared at "
                f"{DIMENSIONS}, bge-m3's width"
            )
        return vectors

    async def _the_model(self) -> str:
        """The model's id as TEI reports it, asked once and kept for the refusal's sentence."""
        if self._model is None:
            answer = await self._http.get(f"{self._url}/info")
            answer.raise_for_status()
            info: Any = answer.json()
            self._model = str(info.get("model_id") or UNNAMED)
        return self._model
