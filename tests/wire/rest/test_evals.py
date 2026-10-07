"""Tests for the bodies of the eval doors."""

import pytest
from pydantic import ValidationError

from pinecall.domain.errors import DeclarationRefused
from pinecall.wire.parts import ModelConfig
from pinecall.wire.rest.evals import (
    CallerPersona,
    Golden,
    JudgeRequest,
    NextLineRequest,
    PersonaRequest,
    PlaceVoiceCallRequest,
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


def test_a_persona_written_with_its_goal_and_style_alone_says_nothing_else() -> None:
    written = PersonaRequest.read({"goal": "g", "style": "s"}, "persona")
    assert (written.about, written.facts, written.state, written.was) == ("", {}, {}, None)


def test_a_persona_naming_the_agents_it_calls_is_refused_since_it_is_one_agents() -> None:
    with pytest.raises(DeclarationRefused):
        PersonaRequest.read({"goal": "g", "style": "s", "agents": ["recepcion"]}, "persona")


def test_a_judge_that_says_nothing_of_when_it_runs_reads_every_call() -> None:
    assert JudgeRequest.read({"question": "q"}, "judge").runs_on == "every-call"


def test_a_judge_runs_on_every_call_or_on_simulations_and_nothing_else() -> None:
    assert JudgeRequest.read({"question": "q", "runs_on": "simulations"}, "judge").runs_on == (
        "simulations"
    )
    with pytest.raises(DeclarationRefused):
        JudgeRequest.read({"question": "q", "runs_on": "sometimes"}, "judge")


def test_the_simulated_caller_the_box_pays_for_has_a_ceiling() -> None:
    persona = CallerPersona(goal="book", style="brief")
    assert PlaceVoiceCallRequest(call="call_c", agent="a", persona=persona, turns=40).turns == 40
    with pytest.raises(ValidationError):
        PlaceVoiceCallRequest(call="call_c", agent="a", persona=persona, turns=41)
    with pytest.raises(ValidationError):
        NextLineRequest(persona=persona, turns_left=1_000)
    with pytest.raises(ValidationError):
        RunSuiteRequest(agent="a", goldens=[Golden(name=f"g{n}") for n in range(201)])
    suite = RunSuiteRequest(agent="a", goldens=[Golden(name=f"g{n}") for n in range(200)])
    assert len(suite.goldens) == 200
    model = ModelConfig(provider="acme", model="acme-2")
    with pytest.raises(ValidationError):
        RunSuiteRequest(agent="a", models=[model] * 9)
    with pytest.raises(ValidationError):
        RunSuiteRequest(agent="a", cases=[f"c{n}" for n in range(201)])
    with pytest.raises(ValidationError):
        Golden(name="g", input=["hola"] * 41)
    assert len(Golden(name="g", input=["hola"] * 40).input) == 40
