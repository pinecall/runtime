"""TEI, the embedder the box runs: bge-m3 behind Hugging Face's text-embeddings-inference."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx

from pinecall.providers.embedder import (
    DIMENSIONS,
    EmbedderUnreachable,
    WrongWidth,
    every_chunk_on_its_own,
)

# TEI's own name for a model it was not told, so a refusal always has a word to say.
UNNAMED = "the embedder at TEI_URL"

# What a lookup's error entry says when the embedder is down: the vendor, the URL, the reason.
DID_NOT_ANSWER = "TEI at {url} did not answer: {why}"

# A push embeds a whole folder and is on nobody's clock; a lookup is on a caller's and has a budget
# of its own that cancels it (session/lookup_tools.py). The http client's default timeout is a
# lookup's five seconds, and the first batch a cold CPU embedder sees — bge-m3 warming up on a
# laptop — takes longer than that: the first `knowledge push` of the day timed out and the second
# went through (2026-09-11). So a batch of a push waits this long, and a batch of a lookup waits the
# client's default as before.
A_PUSH_MAY_TAKE_S = 120.0


# One per process, over the one http client the process opened. /info names the model and is
# read once; the width is measured on the vectors themselves, because /info does not carry it —
# a model of another width is refused at the first embed, by name, before a row is written.
# Nothing here talks to TEI until a vector is needed: a gateway with no TEI still starts.
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
        return await self._embed(texts, timeout=None)

    async def _embed(self, texts: Sequence[str], *, timeout: float | None) -> list[list[float]]:
        """One batch, with a lookup's patience or a push's."""
        if not texts:
            return []
        model = await self.model()
        answer = await self._asked(
            "POST", "/embed", {"inputs": list(texts), "truncate": True}, timeout=timeout
        )
        vectors: list[list[float]] = answer.json()
        if vectors and len(vectors[0]) != DIMENSIONS:
            raise WrongWidth(
                f"{model} answers {len(vectors[0])}-wide vectors; the tables are declared at "
                f"{DIMENSIONS}, bge-m3's width"
            )
        return vectors

    # bge-m3 embeds one text at a time whatever it is handed, so a document is nothing to it but
    # an order to keep; the contextual embedders are the ones that read the neighbours.
    async def embed_documents(self, documents: Sequence[Sequence[str]]) -> list[list[list[float]]]:
        """Every chunk of every document, batched flat and cut back where the documents were."""

        async def a_patient_batch(texts: Sequence[str]) -> list[list[float]]:
            return await self._embed(texts, timeout=A_PUSH_MAY_TAKE_S)

        return await every_chunk_on_its_own(a_patient_batch, documents)

    async def model(self) -> str:
        """The model's id as TEI reports it, asked once and kept for the refusal's sentence."""
        if self._model is None:
            info: Any = (await self._asked("GET", "/info")).json()
            self._model = str(info.get("model_id") or UNNAMED)
        return self._model

    # A connection refused, a timeout and a 5xx are one fact to a lookup — the embedder is down —
    # and the sentence names TEI and its URL, so the error entry on the call's log does too.
    async def _asked(
        self, method: str, path: str, body: Any = None, *, timeout: float | None = None
    ) -> httpx.Response:
        """One request to TEI, answered 2xx; anything else is EmbedderUnreachable, by name."""
        url = f"{self._url}{path}"
        patience = httpx.USE_CLIENT_DEFAULT if timeout is None else httpx.Timeout(timeout)
        try:
            answer = await self._http.request(method, url, json=body, timeout=patience)
            answer.raise_for_status()
        except httpx.HTTPError as failed:
            why = str(failed) or type(failed).__name__
            raise EmbedderUnreachable(DID_NOT_ANSWER.format(url=self._url, why=why)) from failed
        return answer
