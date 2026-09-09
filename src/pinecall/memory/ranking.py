"""How recalled facts are ordered: two branches fused by rank, then how recent and how sure."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from pinecall.types import Fact

# Reciprocal rank fusion's constant, as Cormack et al. set it: a rank weighs 1 / (60 + rank), so
# the first of one branch and the tenth of the other sit close, and nothing hinges on raw scores
# two branches measure on scales of their own — a cosine distance and a negative BM25.
RRF_K = 60

# How many candidates each branch hands the fusion: the design's thirty.
CANDIDATES_PER_BRANCH = 30

# A fact learned ninety days ago weighs half of one learned today — ACT-R's decay, as the design
# took it from NOOA: what was said recently is what the next call is about.
HALF_LIFE_DAYS = 90.0

SECONDS_PER_DAY = 86_400.0


@dataclass(frozen=True)
class Candidate:
    """One fact as a branch found it, and how sure memory was of it when it was written."""

    fact: Fact
    confidence: float


# The score a caller reads is relative: the best candidate is 1.0 and the rest are their share of
# it, so a view's min_score means the same thing whatever the branches measured that turn.
def ranked(
    dense: Sequence[Candidate], sparse: Sequence[Candidate], *, now: datetime, k: int
) -> list[Fact]:
    """The two branches fused by rank, weighed by recency and confidence, the best at 1.0, top k."""
    by_id = {candidate.fact.id: candidate for candidate in (*dense, *sparse)}
    fused = reciprocal_rank_fusion(
        [candidate.fact.id for candidate in dense], [candidate.fact.id for candidate in sparse]
    )
    weighed = {
        id: score * recency(by_id[id].fact.valid_from, now) * by_id[id].confidence
        for id, score in fused.items()
    }
    best = max(weighed.values(), default=0.0)
    ordered = sorted(weighed, key=lambda id: (-weighed[id], id))
    return [
        replace(by_id[id].fact, score=weighed[id] / best if best else 0.0) for id in ordered[:k]
    ]


def reciprocal_rank_fusion(*orders: Sequence[str], k: int = RRF_K) -> Mapping[str, float]:
    """Each id's summed 1 / (k + rank) over every order it appears in; ranks count from one."""
    fused: dict[str, float] = {}
    for order in orders:
        for rank, id in enumerate(order, start=1):
            fused[id] = fused.get(id, 0.0) + 1.0 / (k + rank)
    return fused


def recency(learned: datetime, now: datetime) -> float:
    """1.0 for a fact learned now, halving every HALF_LIFE_DAYS; a clock skew never weighs more."""
    age_days = max((now - learned).total_seconds(), 0.0) / SECONDS_PER_DAY
    return math.pow(0.5, age_days / HALF_LIFE_DAYS)
