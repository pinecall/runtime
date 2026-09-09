"""How recalled facts are ordered: two branches fused by rank, then how recent and how sure."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from pinecall.types import Fact, reciprocal_rank_fusion, relative_to_the_best

# A fact learned ninety days ago weighs half of one learned today — ACT-R's decay, as the design
# took it from NOOA: what was said recently is what the next call is about.
HALF_LIFE_DAYS = 90.0

SECONDS_PER_DAY = 86_400.0


@dataclass(frozen=True)
class Candidate:
    """One fact as a branch found it, and how sure memory was of it when it was written."""

    fact: Fact
    confidence: float


# The fusion and the relative score are types/fusion.py's, shared with the knowledge base; what
# is memory's own is the weighing in between: how recent a fact is, and how sure memory was of it.
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
    scored = relative_to_the_best(weighed)
    return [replace(by_id[id].fact, score=score) for id, score in list(scored.items())[:k]]


def recency(learned: datetime, now: datetime) -> float:
    """1.0 for a fact learned now, halving every HALF_LIFE_DAYS; a clock skew never weighs more."""
    age_days = max((now - learned).total_seconds(), 0.0) / SECONDS_PER_DAY
    return math.pow(0.5, age_days / HALF_LIFE_DAYS)
