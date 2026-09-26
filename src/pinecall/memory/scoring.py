"""How well memory answered its own golden: which facts came back, recall at k and nDCG at ten."""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from pinecall.types import Fact
from pinecall.types.golden_scores import Score as GoldenScore
from pinecall.types.golden_scores import golden_score


@dataclass(frozen=True)
class Question:
    """One question of a golden: what memory holds, what the caller said, what should come back."""

    holds: Sequence[str]
    asks: str
    expects: Sequence[str]


@dataclass(frozen=True)
class Answered:
    """One question and the facts memory returned for it, best first."""

    question: Question
    facts: Sequence[Fact]

    @property
    def found(self) -> tuple[str, ...]:
        """The facts that came back, in the order memory ranked them."""
        return tuple(fact.text for fact in self.facts)

    # Only the FIRST fact that says it counts: memory that returns the same thing twice has
    # answered once and filled the rest of `k` with itself.
    @property
    def ranks(self) -> tuple[int | None, ...]:
        """Where each expected fact came back, counting from one; None for one that never did."""
        return tuple(self._rank(expected) for expected in self.question.expects)

    @property
    def missing(self) -> tuple[str, ...]:
        """Every fact the question expected and did not get back, in the order it asked for them."""
        return tuple(
            expected
            for expected, rank in zip(self.question.expects, self.ranks, strict=True)
            if rank is None
        )

    def _rank(self, expected: str) -> int | None:
        """The place one expected fact came back at, or nothing when it did not."""
        for at, text in enumerate(self.found, start=1):
            if says(text, expected):
                return at
        return None


type Score = GoldenScore[Answered]


# A fact is a sentence a model wrote, so a golden cannot be held to its wording: what the person
# writing one knows is the substance — "prefiere mañanas" — and the row may say it at length. So a
# fact answers when what came back CONTAINS what was expected, both folded: accents dropped,
# because a golden typed on one keyboard and a transcript written by an STT disagree about them
# and the BM25 configuration behind the recall folds them too; case folded; and runs of whitespace
# collapsed, so a golden may be written across two lines. Containment in that direction only — a
# row that says LESS than the golden asked for ("alérgica", for an expected "alérgica a la
# penicilina") is not the fact, and equality either way would make every golden brittle.
def says(found: str, expected: str) -> bool:
    """Whether a fact that came back is the one a golden expected."""
    return _folded(expected) in _folded(found)


def _folded(text: str) -> str:
    """One text as a match reads it: no accents, no case, one space between words."""
    letters = unicodedata.normalize("NFD", text)
    return " ".join("".join(c for c in letters if not unicodedata.combining(c)).casefold().split())


# What is memory's own is which fact answered and how many a question may want; the arithmetic — the
# share found, the logarithmic discount — is types/golden_scores.py's, shared with the knowledge
# base.
def scored(answered: Sequence[Answered], k: int) -> Score:
    """The golden's two figures, and every question memory did not answer whole."""
    return golden_score(answered, k, lambda one: one.ranks)
