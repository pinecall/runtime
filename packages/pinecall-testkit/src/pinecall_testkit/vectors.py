"""A deterministic embedder for every test that needs vectors and no TEI: the words decide."""

import hashlib
import math
import re
from collections.abc import Sequence

from pinecall.providers.embedder import DIMENSIONS, every_chunk_on_its_own

A_WORD = re.compile(r"\w+")

# How many slots one word lights: enough that two texts sharing a word share direction, few
# enough that a thousand words do not fill the vector.
SLOTS_PER_WORD = 4

# What this embedder answers for its model: a word no vendor would report.
HASH_MODEL = "hash-of-the-words"


class HashEmbedder:
    """1024-d unit vectors from a sha256 over each word: texts sharing words land close."""

    dimensions = DIMENSIONS

    async def model(self) -> str:
        """The name a row keeps beside these vectors, so a test can read it back."""
        return HASH_MODEL

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """One vector per text, the same vector for the same words, whatever the order."""
        return [a_vector(text) for text in texts]

    async def embed_documents(self, documents: Sequence[Sequence[str]]) -> list[list[list[float]]]:
        """The Protocol's other half: this embedder reads no neighbours, so it only keeps order."""
        return await every_chunk_on_its_own(self.embed, documents)


def a_vector(text: str) -> list[float]:
    """The vector of one text: every word adds ±1 at four hashed slots; the sum, at unit length."""
    vector = [0.0] * DIMENSIONS
    for word in A_WORD.findall(text.lower()):
        digest = hashlib.sha256(word.encode("utf-8")).digest()
        for slot in range(SLOTS_PER_WORD):
            at = int.from_bytes(digest[slot * 4 : slot * 4 + 4], "big") % DIMENSIONS
            sign = 1.0 if digest[16 + slot] % 2 == 0 else -1.0
            vector[at] += sign
    length = math.sqrt(sum(value * value for value in vector))
    if length == 0.0:
        vector[0] = 1.0
        return vector
    return [value / length for value in vector]


def cosine(one: Sequence[float], other: Sequence[float]) -> float:
    """How alike two vectors point: 1 the same, 0 unrelated, for a test that ranks."""
    return sum(a * b for a, b in zip(one, other, strict=True))
