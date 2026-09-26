"""Reciprocal rank fusion: how two branches of a hybrid search become one order, no IO."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

# Reciprocal rank fusion's constant, as Cormack et al. set it: a rank weighs 1 / (60 + rank), so
# the first of one branch and the tenth of the other sit close, and nothing hinges on raw scores
# two branches measure on scales of their own — a cosine distance and a negative BM25.
RRF_K = 60

# How many candidates each branch hands the fusion: the design's thirty. Memory and the knowledge
# base both read it, which is why it lives here and not beside either table.
CANDIDATES_PER_BRANCH = 30


def reciprocal_rank_fusion(*orders: Sequence[str], k: int = RRF_K) -> dict[str, float]:
    """Each id's summed 1 / (k + rank) over every order it appears in; ranks count from one."""
    fused: dict[str, float] = {}
    for order in orders:
        for rank, id in enumerate(order, start=1):
            fused[id] = fused.get(id, 0.0) + 1.0 / (k + rank)
    return fused


# The score a caller reads is relative: the best candidate is 1.0 and the rest are their share of
# it, so a view's min_score means the same thing whatever the branches measured that turn.
def relative_to_the_best(scores: Mapping[str, float]) -> dict[str, float]:
    """Every score divided by the highest, best first; an empty mapping stays empty."""
    best = max(scores.values(), default=0.0)
    ordered = sorted(scores, key=lambda id: (-scores[id], id))
    return {id: scores[id] / best if best else 0.0 for id in ordered}
