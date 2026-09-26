"""The embedder: text in, vectors out, at the one width every halfvec column is declared at."""

from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol

from pgvector import HalfVector

from pinecall._exceptions import PinecallError

# bge-m3's width, which is the width the migrations declare: halfvec(1024) on facts and chunks.
# Perplexity's contextual door is asked for the same 1024 (Matryoshka: the 4b is 2560 unasked),
# which is why they can be swapped in at all.
# An embedder of another width would write vectors no index can read, so it is refused by name.
DIMENSIONS = 1024

# How many texts one request carries when the embedder sees no context and a batch is only a
# batch: large enough that a push is not a thousand round trips, small enough that one refused
# request loses little. The knowledge base used to hold this number; the embedder owns it now,
# because only the embedder knows what a request of its own costs.
TEXTS_PER_BATCH = 32


class WrongWidth(PinecallError):
    """The embedder answered vectors of a width the tables were not declared at."""


# A vector is comparable only to vectors of the same model, so a table that keeps them says whose
# they are; when the two disagree there is nothing to search, and the way out is to push again.
class WrongModel(PinecallError):
    """The rows were written by another model, so nothing this embedder answers can rank them."""


# Raised with the vendor's name and its URL in the sentence, because the sentence is what the
# log carries when a lookup is skipped: "search did not run: TEI at … did not answer".
class EmbedderUnreachable(PinecallError):
    """The embedder did not answer: nothing can be embedded, so nothing can be searched."""


class Embedder(Protocol):
    """What memory and retrieval embed with: one vector per text, all of one width."""

    @property
    def dimensions(self) -> int:
        """How wide every vector is; the columns are declared at exactly this."""
        ...

    # A vector is only comparable to vectors of the same model, so a table that keeps them writes
    # the model's name beside them; the name is the vendor's own, asked of it, never assumed.
    async def model(self) -> str:
        """The model behind the vectors, by the name the vendor reports."""
        ...

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """One vector per text, in the order given."""
        ...

    # What a push asks, and the reason retrieval improved: a chunk embedded while the model sees
    # its neighbours is findable by what the file says around it, not only by its own words.
    async def embed_documents(self, documents: Sequence[Sequence[str]]) -> list[list[list[float]]]:
        """One vector per chunk, one list per document: the order given IS the contract."""
        ...


type Embed = Callable[[Sequence[str]], Awaitable[list[list[float]]]]
"""One flat batch of texts to vectors: what an embedder with no notion of a document offers."""


# The whole of embed_documents for an embedder that sees no context. The documents are only an
# order to keep, so the chunks go out flat, a batch at a time, and come back cut where they were.
async def every_chunk_on_its_own(
    embed: Embed, documents: Sequence[Sequence[str]]
) -> list[list[list[float]]]:
    """embed_documents for a flat embedder: every chunk alone, the documents' shape restored."""
    flat = [chunk for document in documents for chunk in document]
    vectors: list[list[float]] = []
    for start in range(0, len(flat), TEXTS_PER_BATCH):
        vectors.extend(await embed(flat[start : start + TEXTS_PER_BATCH]))
    cut: list[list[list[float]]] = []
    at = 0
    for document in documents:
        cut.append(vectors[at : at + len(document)])
        at += len(document)
    return cut


# Both tables take their vectors as text and cast at the door (`$n::halfvec`), which keeps the
# driver out of the vector type entirely; this is the one place the text form is written.
def halfvec_literal(vector: Sequence[float]) -> str:
    """The vector as halfvec reads it: `[1,0.5,…]`, the text form pgvector itself writes."""
    return HalfVector(list(vector)).to_text()
