"""How well a base answered its own golden: recall at k and nDCG at ten, by code and no model."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from pinecall.types import Chunk

# The separator a heading path is written with, in the chunk and in a golden alike: it is what
# `chunks_as_text` puts in front of every passage, so a person writes what they already read.
BETWEEN_HEADINGS = " › "

# Where the discount stops mattering. Ten is the convention nDCG is named after and it is far past
# any `k` a voice turn uses, so a chunk that ranked eleventh scores nothing and rightly: the model
# never saw it.
AT = 10


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


@dataclass(frozen=True)
class Score:
    """What a golden says about a base, in the two figures that are worth reading."""

    questions: int
    k: int
    recall_at_k: float
    ndcg_at_10: float
    misses: tuple[Answered, ...]


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


# Both figures over the same answers, because they say two different things about one run: recall
# is whether the model could have used the passage at all, nDCG is whether it had to read past
# nine others to get there. A base with recall 1.0 and nDCG 0.4 is one `k` away from being useless.
def scored(answered: Sequence[Answered], k: int) -> Score:
    """The golden's two figures, and every question the base missed."""
    if not answered:
        return Score(questions=0, k=k, recall_at_k=0.0, ndcg_at_10=0.0, misses=())
    ranks = [one.rank for one in answered]
    found = [rank for rank in ranks if rank is not None]
    return Score(
        questions=len(answered),
        k=k,
        recall_at_k=len(found) / len(answered),
        ndcg_at_10=sum(_discounted(rank) for rank in ranks) / len(answered),
        misses=tuple(one for one in answered if one.rank is None),
    )


# One relevant chunk per question, so the ideal DCG is 1 and the normalisation is the discount
# itself. Rank one scores 1.0, rank two 0.63, rank ten 0.29, and past ten nothing at all.
def _discounted(rank: int | None) -> float:
    """What a question contributes to nDCG from the rank its answer landed at."""
    if rank is None or rank > AT:
        return 0.0
    return 1.0 / math.log2(rank + 1)
