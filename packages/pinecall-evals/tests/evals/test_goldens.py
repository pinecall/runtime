"""The golden a run takes: the field a candidate writes, and the mirror of `expect.tools`."""

import pytest

from pinecall.evals.goldens import Golden

pytestmark = pytest.mark.unit


def test_a_promoted_candidate_moved_into_the_goldens_directory_still_runs() -> None:
    """`promoted_from` is provenance, and extra="forbid" would otherwise refuse the file."""
    golden = Golden.model_validate(
        {
            "name": "no-reserva-antes-del-si",
            "promoted_from": "CA_8f4a2c",
            "input": ["Me viene bien la de las cuatro."],
            "expect": {"not_tools": ["book_slot"]},
        }
    )

    assert golden.promoted_from == "CA_8f4a2c"
    assert golden.expect.not_tools == ["book_slot"]


def test_a_golden_a_person_wrote_says_nothing_about_where_it_came_from() -> None:
    """Nothing derives from the field: a hand-written golden simply has none."""
    assert Golden.model_validate({"name": "reserva", "input": ["sí"]}).promoted_from is None


def test_the_mirror_of_expect_tools_is_a_list_of_its_own_and_never_the_phrases() -> None:
    """`not` is about words and `not_tools` about the log: a golden may declare both at once."""
    expect = Golden.model_validate(
        {
            "name": "no-reserva-antes-del-si",
            "input": ["Me viene bien la de las cuatro."],
            "expect": {"not_tools": ["book"], "not": ["queda reservada"]},
        }
    ).expect

    assert expect.not_tools == ["book"]
    assert expect.not_said == ["queda reservada"]


def test_a_golden_that_forbids_no_tool_carries_an_empty_list_and_asks_for_no_judge() -> None:
    assert Golden.model_validate({"name": "reserva", "input": ["sí"]}).expect.not_tools == []
