"""Tests for the bodies of the eval doors."""

import pytest

from pinecall.domain.errors import DeclarationRefused
from pinecall.wire.rest.evals import (
    Golden,
    PersonaRequest,
    RunSuiteRequest,
    ScoreRow,
    Spoken,
)


def test_a_promoted_candidate_moved_into_the_goldens_directory_still_runs() -> None:
    golden = Golden.read(
        {
            "name": "no-reserva-antes-del-si",
            "promoted_from": "CA_8f4a2c",
            "input": ["Me viene bien la de las cuatro."],
            "expect": {"not_tools": ["book_slot"]},
        },
        "golden",
    )
    assert golden.promoted_from == "CA_8f4a2c"
    assert golden.expect.not_tools == ["book_slot"]


def test_a_golden_a_person_wrote_says_nothing_about_where_it_came_from() -> None:
    assert Golden.read({"name": "reserva", "input": ["sí"]}, "golden").promoted_from is None


def test_the_mirror_of_expect_tools_is_a_list_of_its_own_and_never_the_phrases() -> None:
    expect = Golden.read(
        {"name": "g", "expect": {"not_tools": ["book"], "not": ["queda reservada"]}}, "golden"
    ).expect
    assert expect.not_tools == ["book"]
    assert expect.not_said == ["queda reservada"]


def test_a_golden_that_forbids_no_tool_carries_an_empty_list_and_asks_for_no_judge() -> None:
    assert Golden.read({"name": "reserva", "input": ["sí"]}, "golden").expect.not_tools == []


def test_the_register_a_golden_expects_travels_under_the_wires_own_word() -> None:
    golden = Golden.read({"name": "g", "expect": {"register": "usted"}}, "golden")
    assert golden.expect.addressed_as == "usted"
    assert golden.expect.written()["register"] == "usted"


def test_a_golden_with_a_key_nobody_knows_is_refused_by_what_did_not_fit() -> None:
    with pytest.raises(DeclarationRefused, match="golden"):
        Golden.read({"name": "g", "expcet": {}}, "golden")


def test_a_suite_is_a_clean_line_unless_it_says_otherwise_and_loses_no_more_than_all() -> None:
    suite = RunSuiteRequest.read({"agent": "clinica-norte", "goldens": []}, "suite")
    assert (suite.voice, suite.interferer_db, suite.packet_loss, suite.models) == (
        False,
        None,
        0.0,
        [],
    )
    with pytest.raises(DeclarationRefused):
        RunSuiteRequest.read({"agent": "a", "goldens": [], "packet_loss": 1.5}, "suite")


def test_a_cell_that_held_says_nothing_of_requests_on_the_wire() -> None:
    row = ScoreRow(model="m", golden="g", scores=[], summary=None)
    assert "asked" not in row.written()


def test_a_turn_the_caller_heard_is_said_by_the_agent_or_by_the_caller_and_nobody_else() -> None:
    with pytest.raises(DeclarationRefused):
        Spoken.read({"who": "narrator", "said": "hola"}, "heard")


def test_a_persona_written_for_nobody_in_particular_calls_every_agent() -> None:
    written = PersonaRequest.read({"goal": "g", "style": "s"}, "persona")
    assert written.agents == []
    assert (written.about, written.facts, written.state) == ("", {}, {})
