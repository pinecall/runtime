"""Reciprocal rank fusion, the one definition both tables rank with, and the relative score."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from conftest import SEARCH_S
from pinecall.types import RRF_K, reciprocal_rank_fusion, relative_to_the_best

pytestmark = pytest.mark.unit


def test_a_rank_weighs_one_over_k_plus_rank_and_an_id_in_both_orders_adds_up() -> None:
    fused = reciprocal_rank_fusion(["a", "b"], ["b"])
    assert fused["a"] == pytest.approx(1 / (RRF_K + 1))
    assert fused["b"] == pytest.approx(1 / (RRF_K + 2) + 1 / (RRF_K + 1))


def test_the_id_both_orders_name_outranks_the_first_of_either_alone() -> None:
    fused = reciprocal_rank_fusion(["dense", "both"], ["sparse", "both"])
    assert max(fused, key=fused.__getitem__) == "both"


def test_the_best_is_one_and_the_rest_are_their_share_of_it_best_first() -> None:
    scored = relative_to_the_best({"b": 0.5, "a": 1.0, "c": 0.25})
    assert list(scored.items()) == [("a", 1.0), ("b", 0.5), ("c", 0.25)]


def test_a_tie_is_ordered_by_id_so_two_processes_answer_alike() -> None:
    assert list(relative_to_the_best({"z": 1.0, "m": 1.0})) == ["m", "z"]


def test_nothing_scored_is_nothing_and_a_zero_best_divides_nothing() -> None:
    assert relative_to_the_best({}) == {}
    assert relative_to_the_best({"a": 0.0}) == {"a": 0.0}


AN_ORDER = st.lists(st.sampled_from("abcdefgh"), unique=True)
SOME_ORDERS = st.lists(AN_ORDER, min_size=1, max_size=4)


@pytest.mark.timeout(SEARCH_S)
@given(orders=SOME_ORDERS)
def test_the_fusion_is_blind_to_which_branch_came_first(orders: list[list[str]]) -> None:
    """Equal to a float's rounding: the sum is the same sum, taken in the other order."""
    assert reciprocal_rank_fusion(*orders) == pytest.approx(
        reciprocal_rank_fusion(*reversed(orders))
    )


@pytest.mark.timeout(SEARCH_S)
@given(orders=SOME_ORDERS, more=AN_ORDER)
def test_one_more_branch_never_lowers_anybody(orders: list[list[str]], more: list[str]) -> None:
    before, after = reciprocal_rank_fusion(*orders), reciprocal_rank_fusion(*orders, more)
    assert all(after[id] >= score for id, score in before.items())


@pytest.mark.timeout(SEARCH_S)
@given(orders=SOME_ORDERS)
def test_the_relative_score_tops_at_one_and_reads_best_first(orders: list[list[str]]) -> None:
    relative = relative_to_the_best(reciprocal_rank_fusion(*orders))
    scores = list(relative.values())
    assert scores == sorted(scores, reverse=True)
    assert not scores or scores[0] == 1.0
