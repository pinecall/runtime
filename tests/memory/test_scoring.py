"""What a golden says about memory: which facts came back, the two figures, and what was missing."""

import pytest

from pinecall.memory.scoring import Answered, Question, says, score_golden
from tests.memory.facts import a_fact

pytestmark = pytest.mark.unit

MORNINGS = "Prefiere mañanas"
PENICILLIN = "Alérgica a la penicilina"
VIDAL = "Paciente de la doctora Vidal desde 2024"


def asked(expects: list[str], *found: str) -> Answered:
    """One question and the facts memory returned for it, best first."""
    return Answered(
        question=Question(
            holds=[MORNINGS, PENICILLIN], asks="¿le va bien el martes?", expects=expects
        ),
        facts=[a_fact(f"f{at}", text) for at, text in enumerate(found)],
    )


# ── what counts as the fact the golden asked for ────────────────────────────────


def test_a_fact_says_what_was_expected_when_it_is_the_same_sentence() -> None:
    assert says(MORNINGS, MORNINGS)


def test_a_fact_that_says_more_than_the_golden_asked_for_is_still_the_fact() -> None:
    assert says("Prefiere mañanas, nunca después de comer", MORNINGS)


def test_a_fact_that_says_less_than_the_golden_asked_for_is_not_the_fact() -> None:
    assert not says("Alérgica", PENICILLIN)


def test_accents_and_case_are_not_what_a_golden_is_held_to() -> None:
    assert says("prefiere MANANAS para las citas", MORNINGS)
    assert says(PENICILLIN, "alergica a la penicilina")


def test_a_golden_written_across_two_lines_matches_a_fact_written_on_one() -> None:
    assert says(VIDAL, "Paciente de la doctora\n   Vidal desde 2024")


def test_another_fact_of_the_same_contact_is_not_the_one_that_was_expected() -> None:
    assert not says(PENICILLIN, MORNINGS)


# ── the two figures ─────────────────────────────────────────────────────────────


def test_the_fact_that_came_back_first_scores_everything_and_misses_nothing() -> None:
    score = score_golden([asked([MORNINGS], MORNINGS, PENICILLIN)], k=6)
    assert (score.questions, score.k) == (1, 6)
    assert (score.recall_at_k, score.ndcg_at_10) == (1.0, 1.0)
    assert score.misses == ()


def test_a_fact_that_came_back_second_counts_for_recall_and_costs_the_ranking() -> None:
    score = score_golden([asked([MORNINGS], PENICILLIN, MORNINGS)], k=6)
    assert score.recall_at_k == 1.0
    assert score.ndcg_at_10 == pytest.approx(0.6309, abs=0.001)


def test_a_question_memory_did_not_answer_names_what_was_missing_and_what_came_instead() -> None:
    score = score_golden([asked([MORNINGS], PENICILLIN, VIDAL)], k=6)
    assert (score.recall_at_k, score.ndcg_at_10) == (0.0, 0.0)
    (missed,) = score.misses
    assert missed.missing == (MORNINGS,)
    assert missed.found == (PENICILLIN, VIDAL)


def test_a_question_that_wants_two_facts_and_got_one_of_them_is_half_recalled_and_a_miss() -> None:
    score = score_golden([asked([MORNINGS, PENICILLIN], MORNINGS, VIDAL)], k=6)
    assert score.recall_at_k == 0.5
    (missed,) = score.misses
    assert missed.missing == (PENICILLIN,)


def test_only_the_first_fact_that_says_it_counts_so_memory_that_repeats_itself_answered_once() -> (
    None
):
    score = score_golden([asked([MORNINGS], MORNINGS, MORNINGS, MORNINGS)], k=6)
    assert score.ndcg_at_10 == 1.0


def test_the_figures_are_the_share_over_every_question_asked() -> None:
    score = score_golden(
        [asked([MORNINGS], MORNINGS), asked([PENICILLIN], MORNINGS)],
        k=6,
    )
    assert (score.questions, score.recall_at_k, score.ndcg_at_10) == (2, 0.5, 0.5)


def test_a_golden_with_no_questions_scores_nothing_and_says_so() -> None:
    score = score_golden([], k=6)
    assert (score.questions, score.recall_at_k, score.misses) == (0, 0.0, ())
