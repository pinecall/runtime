"""What a golden says about a base: the two figures, and the questions it missed."""

import pytest

from pinecall.knowledge.scoring import AT, Answered, Question, answers, scored, where
from pinecall.types import Chunk

pytestmark = pytest.mark.unit

TARIFAS = "tarifas.md"
REVISION = "Tarifas › Revisión"


def a_chunk(path: str = TARIFAS, heading: str | None = REVISION) -> Chunk:
    """One chunk as a search hands it back; only where it came from matters to a golden."""
    return Chunk(id="c", base="clinica", path=path, heading=heading, text="…", score=1.0)


def asked(expects: str, *found: Chunk) -> Answered:
    """One question and the chunks a base returned for it, best first."""
    return Answered(question=Question(asks="¿cuánto cuesta?", expects=expects), chunks=found)


def test_a_chunks_heading_path_is_the_file_and_the_headings_under_it() -> None:
    assert where(a_chunk()) == f"{TARIFAS} › {REVISION}"
    assert where(a_chunk(heading=None)) == TARIFAS


def test_naming_a_file_alone_accepts_any_chunk_of_it() -> None:
    assert answers(f"{TARIFAS} › {REVISION}", TARIFAS)


def test_naming_a_heading_accepts_that_section_and_what_is_under_it() -> None:
    assert answers(f"{TARIFAS} › Tarifas › Revisión", "tarifas.md › Tarifas")
    assert answers(f"{TARIFAS} › {REVISION}", f"{TARIFAS} › {REVISION}")


def test_a_file_never_answers_for_a_file_whose_name_merely_starts_the_same() -> None:
    assert not answers("tarifas-2024.md › Revisión", TARIFAS)


def test_a_question_whose_chunk_came_back_first_scores_everything() -> None:
    score = scored([asked(TARIFAS, a_chunk())], k=4)
    assert score.recall_at_k == 1.0
    assert score.ndcg_at_10 == 1.0
    assert score.misses == ()


def test_a_question_whose_chunk_came_back_second_still_counts_for_recall() -> None:
    score = scored([asked(TARIFAS, a_chunk(path="otro.md"), a_chunk())], k=4)
    assert score.recall_at_k == 1.0
    assert score.ndcg_at_10 == pytest.approx(0.6309, abs=0.001)


def test_a_question_whose_chunk_never_came_back_is_a_miss_the_person_can_read() -> None:
    score = scored([asked(TARIFAS, a_chunk(path="otro.md", heading="Horarios"))], k=4)
    assert score.recall_at_k == 0.0
    assert score.ndcg_at_10 == 0.0
    (missed,) = score.misses
    assert missed.found == ("otro.md › Horarios",)


def test_only_the_first_match_counts_so_a_base_that_repeats_itself_answered_once() -> None:
    score = scored([asked(TARIFAS, a_chunk(), a_chunk(), a_chunk())], k=4)
    assert score.ndcg_at_10 == 1.0


def test_a_chunk_past_the_tenth_scores_nothing_because_the_model_never_saw_it() -> None:
    tail = [a_chunk(path=f"otro-{n}.md") for n in range(AT)] + [a_chunk()]
    score = scored([asked(TARIFAS, *tail)], k=len(tail))
    assert score.recall_at_k == 1.0
    assert score.ndcg_at_10 == 0.0


def test_the_two_figures_are_the_share_over_every_question_asked() -> None:
    score = scored(
        [asked(TARIFAS, a_chunk()), asked(TARIFAS, a_chunk(path="otro.md"))],
        k=4,
    )
    assert score.questions == 2
    assert score.recall_at_k == 0.5
    assert score.ndcg_at_10 == 0.5


def test_a_golden_with_no_questions_scores_nothing_and_says_so() -> None:
    score = scored([], k=4)
    assert score.questions == 0
    assert score.recall_at_k == 0.0
