"""The embedder: text in, vectors out, at the one width every halfvec column is declared at."""

from collections.abc import Sequence
from typing import Protocol

from pgvector import HalfVector

from pinecall._exceptions import PinecallError

# bge-m3's width, which is the width the migrations declare: halfvec(1024) on facts and chunks.
# An embedder of another width would write vectors no index can read, so it is refused by name.
DIMENSIONS = 1024


class WrongWidth(PinecallError):
    """The embedder answered vectors of a width the tables were not declared at."""


# Raised with the vendor's name and its URL in the sentence, because the sentence is what the
# log carries when a fill is skipped: "retrieval was not filled: TEI at … did not answer".
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


# Both tables take their vectors as text and cast at the door (`$n::halfvec`), which keeps the
# driver out of the vector type entirely; this is the one place the text form is written.
def as_halfvec(vector: Sequence[float]) -> str:
    """The vector as halfvec reads it: `[1,0.5,…]`, the text form pgvector itself writes."""
    return HalfVector(list(vector)).to_text()
