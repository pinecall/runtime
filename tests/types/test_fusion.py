"""Reciprocal rank fusion, the one definition both tables rank with, and the relative score."""

import pytest

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
