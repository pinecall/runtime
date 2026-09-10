"""The two figures any golden answers, from where the answers it wanted ranked. No IO, no model."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import fmean

# Where the discount stops mattering. Ten is the convention nDCG is named after and it is far past
# any k a voice turn uses, so an answer that ranked eleventh scores nothing and rightly: the model
# never saw it.
AT = 10


@dataclass(frozen=True)
class Figures:
    """What a golden says about one run, in the two numbers that are worth reading."""

    recall_at_k: float
    ndcg_at_10: float


# Both figures over the same ranks, because they say two different things about one run: recall is
# whether the model could have used the answer at all, nDCG is whether it had to read past nine
# others to get there. A run with recall 1.0 and nDCG 0.4 is one k away from being useless.
# The knowledge base wants one chunk per question and memory may want several facts, which is the
# whole difference between them: a question that wants one is normalised by a perfect first place,
# a question that wants three by the best three places there are.
def figures(asked: Sequence[Sequence[int | None]]) -> Figures:
    """One entry per question: where each answer it wanted ranked, None for one that never came."""
    if not asked:
        return Figures(recall_at_k=0.0, ndcg_at_10=0.0)
    return Figures(
        recall_at_k=fmean(_share_found(ranks) for ranks in asked),
        ndcg_at_10=fmean(_normalised(ranks) for ranks in asked),
    )


def _share_found(ranks: Sequence[int | None]) -> float:
    """The share of what one question wanted that came back at all."""
    if not ranks:
        return 0.0
    return len([rank for rank in ranks if rank is not None]) / len(ranks)


def _normalised(ranks: Sequence[int | None]) -> float:
    """One question's discounted gain over the best it could have been: 1.0 for a perfect order."""
    if not ranks:
        return 0.0
    ideal = sum(_discounted(place) for place in range(1, min(len(ranks), AT) + 1))
    return sum(_discounted(rank) for rank in ranks) / ideal


# Rank one scores 1.0, rank two 0.63, rank ten 0.29, and past the tenth nothing at all.
def _discounted(rank: int | None) -> float:
    """What one wanted answer contributes from the rank it landed at."""
    if rank is None or rank > AT:
        return 0.0
    return 1.0 / math.log2(rank + 1)
