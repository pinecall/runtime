"""The order recalled facts come back in: fused by rank, then recency, then confidence."""

from datetime import timedelta

import pytest

from pinecall.memory.ranking import HALF_LIFE_DAYS, Candidate, ranked, recency
from pinecall.types import RRF_K
from tests.memory.facts import NOW, a_fact

pytestmark = pytest.mark.unit


def test_the_fact_both_branches_find_comes_before_the_ones_only_one_finds() -> None:
    both = Candidate(a_fact("both"), 1.0)
    dense_only = Candidate(a_fact("dense"), 1.0)
    sparse_only = Candidate(a_fact("sparse"), 1.0)
    facts = ranked([dense_only, both], [sparse_only, both], now=NOW, k=6)
    assert [fact.id for fact in facts] == ["both", "dense", "sparse"]


def test_the_best_fact_scores_one_and_the_rest_are_their_share_of_it() -> None:
    facts = ranked(
        [Candidate(a_fact("first"), 1.0), Candidate(a_fact("second"), 1.0)], [], now=NOW, k=6
    )
    assert facts[0].score == 1.0
    assert facts[1].score == pytest.approx((RRF_K + 1) / (RRF_K + 2))


def test_a_fact_learned_a_half_life_ago_weighs_half_of_one_learned_now() -> None:
    assert recency(NOW, NOW) == 1.0
    assert recency(NOW - timedelta(days=HALF_LIFE_DAYS), NOW) == pytest.approx(0.5)
    assert recency(NOW - timedelta(days=2 * HALF_LIFE_DAYS), NOW) == pytest.approx(0.25)


def test_a_fact_dated_after_now_weighs_no_more_than_one() -> None:
    assert recency(NOW + timedelta(days=3), NOW) == 1.0


def test_of_two_facts_at_the_same_rank_the_more_recent_one_comes_first() -> None:
    old = Candidate(a_fact("old", learned=NOW - timedelta(days=180)), 1.0)
    fresh = Candidate(a_fact("fresh"), 1.0)
    facts = ranked([old], [fresh], now=NOW, k=6)
    assert [fact.id for fact in facts] == ["fresh", "old"]
    assert facts[1].score == pytest.approx(0.25)


def test_a_fact_memory_was_less_sure_of_yields_to_one_it_was_sure_of() -> None:
    unsure = Candidate(a_fact("unsure"), 0.5)
    sure = Candidate(a_fact("sure"), 1.0)
    facts = ranked([unsure], [sure], now=NOW, k=6)
    assert [fact.id for fact in facts] == ["sure", "unsure"]
    assert facts[1].score == pytest.approx(0.5)


def test_only_the_k_best_are_answered() -> None:
    dense = [Candidate(a_fact(f"f{n}"), 1.0) for n in range(10)]
    assert [fact.id for fact in ranked(dense, [], now=NOW, k=3)] == ["f0", "f1", "f2"]


def test_no_candidates_is_no_facts() -> None:
    assert ranked([], [], now=NOW, k=6) == []


def test_a_fact_nobody_is_sure_of_at_all_scores_zero_and_divides_nothing() -> None:
    facts = ranked([Candidate(a_fact("none"), 0.0)], [], now=NOW, k=6)
    assert [fact.score for fact in facts] == [0.0]
