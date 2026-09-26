"""How well a base answered its own golden: recall at k and nDCG at ten, by code and no model."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from pinecall.knowledge.chunking import HEADING_SEPARATOR
from pinecall.types import Chunk
from pinecall.types.golden_scores import Score as GoldenScore
from pinecall.types.golden_scores import a_score

# The separator a heading path is written with, in the chunk and in a golden alike: it is what
# `chunks_as_text` puts in front of every passage, so a person writes what they already read.
BETWEEN_HEADINGS = HEADING_SEPARATOR


@dataclass(frozen=True)
class Question:
    """One question of a golden: what somebody asks, and the chunk that should answer it."""

    asks: str
    expects: str


@dataclass(frozen=True)
class Answered:
    """One question and what the base returned for it, best first."""

    question: Question
    chunks: Sequence[Chunk]

    @property
    def found(self) -> tuple[str, ...]:
        """The heading paths that came back, in the order the base ranked them."""
        return tuple(where(chunk) for chunk in self.chunks)

    # The rank the expected chunk landed at, counting from one, or nothing when it never came
    # back. Only the FIRST match counts: a base that returns the same section three times has
    # answered once and filled the rest of `k` with itself.
    @property
    def rank(self) -> int | None:
        """Where the expected chunk ranked, or None when the base did not return it at all."""
        for at, path in enumerate(self.found, start=1):
            if answers(path, self.question.expects):
                return at
        return None


type Score = GoldenScore[Answered]


def where(chunk: Chunk) -> str:
    """A chunk's heading path: the file, then the headings it sits under, as the model reads it."""
    return chunk.path if not chunk.heading else f"{chunk.path}{BETWEEN_HEADINGS}{chunk.heading}"


# A golden is written by the person who wrote the documents, so it must be writable from what they
# can see: naming a file alone accepts any chunk of it, naming a heading accepts that section and
# anything under it. Prefix, not equality, and never a substring — `tarifas.md` must not answer a
# question about `tarifas-2024.md`.
def answers(found: str, expects: str) -> bool:
    """Whether a returned heading path is the one a golden asked for."""
    wanted = expects.strip()
    if found == wanted:
        return True
    return found.startswith(f"{wanted}{BETWEEN_HEADINGS}")


# One relevant chunk per question, which is the arithmetic's simplest case: types/golden_scores.py
# does both figures, here and for memory, and what is the knowledge base's own is which chunk
# answered.
def scored(answered: Sequence[Answered], k: int) -> Score:
    """The golden's two figures, and every question the base missed."""
    return a_score(answered, k, lambda one: (one.rank,))
