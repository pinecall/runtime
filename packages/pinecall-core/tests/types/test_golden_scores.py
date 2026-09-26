"""The two figures every golden answers, over the ranks its wanted answers came back at."""

import pytest

from pinecall.types.golden_scores import AT, figures

pytestmark = pytest.mark.unit


def test_a_golden_with_no_questions_answers_nothing_rather_than_dividing_by_none() -> None:
    found = figures([])
    assert (found.recall_at_k, found.ndcg_at_10) == (0.0, 0.0)


def test_one_answer_that_came_back_first_scores_everything() -> None:
    found = figures([(1,)])
    assert (found.recall_at_k, found.ndcg_at_10) == (1.0, 1.0)


def test_one_answer_that_came_back_second_still_counts_for_recall() -> None:
    found = figures([(2,)])
    assert found.recall_at_k == 1.0
    assert found.ndcg_at_10 == pytest.approx(0.6309, abs=0.001)


def test_an_answer_past_the_tenth_scores_nothing_because_the_model_never_saw_it() -> None:
    found = figures([(AT + 1,)])
    assert found.recall_at_k == 1.0
    assert found.ndcg_at_10 == 0.0


def test_the_two_figures_are_the_share_over_every_question_asked() -> None:
    found = figures([(1,), (None,)])
    assert (found.recall_at_k, found.ndcg_at_10) == (0.5, 0.5)


# What memory needs and the knowledge base does not: a question may want more than one answer.
def test_a_question_that_wants_two_facts_and_got_the_top_two_is_perfect() -> None:
    found = figures([(1, 2)])
    assert (found.recall_at_k, found.ndcg_at_10) == (1.0, 1.0)


def test_a_question_that_wants_two_and_got_one_of_them_is_half_recalled() -> None:
    found = figures([(1, None)])
    assert found.recall_at_k == 0.5
    assert found.ndcg_at_10 == pytest.approx(0.6131, abs=0.001)


def test_two_wanted_facts_ranked_low_are_recalled_whole_and_ordered_badly() -> None:
    found = figures([(5, 6)])
    assert found.recall_at_k == 1.0
    assert found.ndcg_at_10 == pytest.approx(0.4556, abs=0.001)


def test_a_question_that_wanted_nothing_scores_nothing_and_raises_nothing() -> None:
    found = figures([()])
    assert (found.recall_at_k, found.ndcg_at_10) == (0.0, 0.0)
