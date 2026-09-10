"""Perplexity's embedders over HTTP — the flat one and the contextual one — and OpenRouter's."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any, NoReturn

import httpx

from pinecall.providers.embed.wire import (
    SIGNED_BYTES,
    at_unit_length,
    embeddings_under,
    what_the_endpoint_said,
)
from pinecall.providers.embedder import (
    DIMENSIONS,
    EmbedderUnreachable,
    WrongWidth,
    every_chunk_on_its_own,
)
from pinecall.types.counting import estimated_tokens

# What tells the two models apart, and it is the model's own name: `pplx-embed-context-v1-0.6b`
# reads a document's chunks together, `pplx-embed-v1-0.6b` reads each text alone. Nothing else in
# the configuration says which door to knock at, so nothing else can disagree with the name.
CONTEXTUAL = "-context-"

# The contextual door's context is 32 768 tokens and it counts the whole WINDOW against it — the
# chunks are concatenated before the model reads them — so a document goes out in windows
# measured in tokens and never in chunks: forty chunks of one file and forty of another are
# nothing alike. 24 000 leaves the margin an estimate is entitled to be wrong by.
WINDOW_TOKENS = 24_000

# The two doors, under one base URL.
FLAT = "/embeddings"
CONTEXTUALIZED = "/contextualizedembeddings"

# What a fill's error entry says when the embedder refuses: the vendor, the door, and the reason
# in the endpoint's own words when it gave any — a 400 that says `Invalid model` must not read
# as a timeout.
DID_NOT_ANSWER = "{vendor} at {url} did not answer: {why}"

# A reply of the wrong count is a reply nothing can be done with: its vectors would be attached
# to the wrong chunks, silently, and no index would ever say so.
MISCOUNTED = "{answered} vectors for {asked} chunks"


# One per process, over the one http client the process opened. The model is configuration and is
# never asked of the vendor: these APIs have no /info, and a name that could drift from the name
# in a base's row would make that row a guess. Nothing here opens a socket until a vector is
# needed, so a gateway whose embedder is across the internet starts with the internet down.
class PerplexityEmbedder:
    """One client for both models: the model's name picks the door, the encoding and the shape."""

    def __init__(
        self,
        *,
        vendor: str,
        base_url: str,
        model: str,
        key: str,
        http: httpx.AsyncClient,
        encoding: str = SIGNED_BYTES,
    ) -> None:
        self._vendor = vendor
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._key = key
        self._http = http
        self._encoding = encoding

    @property
    def dimensions(self) -> int:
        """1024, which both models answer at and every halfvec column is declared at."""
        return DIMENSIONS

    @property
    def reads_the_neighbours(self) -> bool:
        """Whether this model sees a document's other chunks: the name is the whole answer."""
        return CONTEXTUAL in self._model

    async def model(self) -> str:
        """The model as it was configured; a base's row keeps this name and is compared to it."""
        return self._model

    # With the contextual model a query has no document to sit inside, so it becomes a document of
    # one chunk: query and chunks then come out of the same model, which is what makes them
    # comparable at all. The flat model asks the flat door and there is nothing to arrange.
    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """One vector per text, in the order given: what a query and a fact are embedded by."""
        if not texts:
            return []
        if self.reads_the_neighbours:
            alone = await self.embed_documents([[text] for text in texts])
            return [vectors[0] for vectors in alone]
        return await self._flat(list(texts))

    async def embed_documents(self, documents: Sequence[Sequence[str]]) -> list[list[list[float]]]:
        """One list per document, one vector per chunk; a document at a time, window by window."""
        if not self.reads_the_neighbours:
            return await every_chunk_on_its_own(self.embed, documents)
        return [await self._document(list(chunks)) for chunks in documents]

    # The flat door's encoding is the VENDOR's, not the door's, and it is measured, not assumed:
    # api.perplexity.ai refuses `float` outright (`encoding format must be one of: base64_int8
    # base64_binary`) while OpenRouter's mirror of the same model answers floats. Either way the
    # request names it, so the reply is decoded as declared and never sniffed.
    async def _flat(self, texts: list[str]) -> list[list[float]]:
        """The flat door: one row per text under `data`, in whichever encoding this vendor takes."""
        body = await self._asked(FLAT, {"input": texts, "encoding_format": self._encoding})
        rows = embeddings_under(body)
        return self._as_many_as([at_unit_length(row) for row in rows], asked=len(texts), door=FLAT)

    async def _document(self, chunks: list[str]) -> list[list[float]]:
        """One file, in windows that fit the model's context; the vectors in the order cut."""
        vectors: list[list[float]] = []
        for window in windows(chunks, WINDOW_TOKENS):
            vectors.extend(await self._window(window))
        return vectors

    # One document per request, so the reply's outer list holds exactly one entry and its own
    # `data` holds the window's chunks. Every vector of every door here comes back unnormalised —
    # MEASURED: 824 long from the contextual door, 746 from Perplexity's flat one, 9 from
    # OpenRouter's — and cosine over an unnormalised vector is a length comparison wearing a
    # similarity's clothes. So `at_unit_length` is on both paths, and it is idempotent.
    async def _window(self, window: list[str]) -> list[list[float]]:
        """One window of one document: int8 in base64, decoded as declared, at unit length."""
        body = await self._asked(
            CONTEXTUALIZED, {"input": [window], "encoding_format": SIGNED_BYTES}
        )
        rows = embeddings_under(body, of_the_first_document=True)
        return self._as_many_as(
            [at_unit_length(row) for row in rows], asked=len(window), door=CONTEXTUALIZED
        )

    # A table declared at one width cannot hold two, and the refusal names the MODEL because the
    # model is what has to change: the reply is well formed and the configuration is not.
    def _as_many_as(
        self, vectors: list[list[float]], *, asked: int, door: str
    ) -> list[list[float]]:
        """As many vectors as chunks, all at the declared width, or the reply is refused whole."""
        if len(vectors) != asked:
            self._refuse(door, MISCOUNTED.format(answered=len(vectors), asked=asked))
        widths = sorted({len(vector) for vector in vectors})
        if widths and widths != [DIMENSIONS]:
            answered = " and ".join(str(width) for width in widths)
            raise WrongWidth(
                f"{self._model} answers {answered}-wide vectors; the tables are declared at "
                f"{DIMENSIONS}, the width every halfvec column holds"
            )
        return vectors

    # A connection refused, a timeout and a refusal are one fact to a fill — nothing can be
    # embedded — and the sentence carries the vendor, the door and, when the endpoint said
    # anything at all, the endpoint's own words.
    async def _asked(self, door: str, said: dict[str, Any]) -> Any:
        """One request, answered 2xx and parsed; anything else is EmbedderUnreachable, by name."""
        headers = {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}
        try:
            answer = await self._http.post(
                f"{self._base_url}{door}", json={"model": self._model, **said}, headers=headers
            )
            answer.raise_for_status()
            return answer.json()
        except httpx.HTTPStatusError as refused:
            self._refuse(door, what_the_endpoint_said(refused.response), refused)
        except (httpx.HTTPError, ValueError) as failed:
            self._refuse(door, str(failed) or type(failed).__name__, failed)

    def _refuse(self, door: str, why: str, cause: Exception | None = None) -> NoReturn:
        """The one sentence every refusal from this vendor reads as, raised where it was found."""
        url = f"{self._base_url}{door}"
        raise EmbedderUnreachable(
            DID_NOT_ANSWER.format(vendor=self._vendor, url=url, why=why)
        ) from cause


def windows(chunks: Sequence[str], budget: int) -> Iterator[list[str]]:
    """One document's chunks grouped so each request fits the context, measured in tokens."""
    window: list[str] = []
    tokens = 0
    for chunk in chunks:
        cost = estimated_tokens(chunk)
        # A chunk over the budget by itself still goes out alone: the endpoint clips it, and a
        # chunk nobody can embed whole is better retrieved truncated than not retrieved at all.
        if window and tokens + cost > budget:
            yield window
            window, tokens = [], 0
        window.append(chunk)
        tokens += cost
    if window:
        yield window
