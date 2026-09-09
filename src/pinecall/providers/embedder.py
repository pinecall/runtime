"""The embedder: text in, vectors out, at the one width every halfvec column is declared at."""

from collections.abc import Sequence
from typing import Protocol

from pinecall._exceptions import PinecallError

# bge-m3's width, which is the width the migrations declare: halfvec(1024) on facts and chunks.
# An embedder of another width would write vectors no index can read, so it is refused by name.
DIMENSIONS = 1024


class WrongWidth(PinecallError):
    """The embedder answered vectors of a width the tables were not declared at."""


class Embedder(Protocol):
    """What memory and retrieval embed with: one vector per text, all of one width."""

    @property
    def dimensions(self) -> int:
        """How wide every vector is; the columns are declared at exactly this."""
        ...

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """One vector per text, in the order given."""
        ...
