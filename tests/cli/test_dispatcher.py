"""The dispatcher: no group prints them all, and an unknown group or verb is a usage error."""

import pytest

from pinecall.cli import GROUPS, build_parser, gateway, main
from pinecall.cli.doctor import verbs as doctor
from pinecall.cli.routes import verbs as routes
from pinecall.cli.sessions import verbs as sessions

pytestmark = pytest.mark.unit


def test_no_arguments_prints_every_group_and_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    printed = capsys.readouterr().out
    for group in GROUPS:
        assert group in printed


def test_an_unknown_group_prints_the_groups_and_exits_two(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as raised:
        main(["orchestra"])
    assert raised.value.code == 2
    complaint = capsys.readouterr().err
    assert "orchestra" in complaint
    assert "doctor" in complaint


def test_an_unknown_verb_of_a_group_exits_two(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        main(["sessions", "levitate"])
    assert raised.value.code == 2
    assert "levitate" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("verb", "runs", "arguments"),
    [
        ("list", routes.run_list, []),
        ("add", routes.run_add, ["+59829000000", "clinica-norte"]),
        ("rm", routes.run_remove, ["+59829000000"]),
        ("seed", routes.run_seed, []),
    ],
)
def test_every_routes_verb_is_wired_to_its_own_function(
    verb: str, runs: object, arguments: list[str]
) -> None:
    parsed = build_parser().parse_args(["routes", verb, *arguments])
    assert parsed.run is runs


def test_routes_with_no_verb_prints_its_verbs(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["routes"]) == 0
    printed = capsys.readouterr().out
    assert all(verb in printed for verb in routes.VERBS)


@pytest.mark.parametrize(
    ("verb", "runs"),
    [("list", sessions.run_list), ("show", sessions.run_show), ("tail", sessions.run_tail)],
)
def test_every_sessions_verb_is_wired_to_its_own_function(verb: str, runs: object) -> None:
    arguments = build_parser().parse_args(["sessions", verb, *(["CA_1"] if verb == "show" else [])])
    assert arguments.run is runs


def test_sessions_with_no_verb_prints_its_verbs(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["sessions"]) == 0
    printed = capsys.readouterr().out
    assert all(verb in printed for verb in sessions.VERBS)


def test_the_gateway_group_is_wired_to_the_server_with_its_defaults() -> None:
    arguments = build_parser().parse_args(["gateway"])
    assert arguments.run is gateway.run
    assert (arguments.host, arguments.port) == (gateway.DEFAULT_HOST, gateway.DEFAULT_PORT)


def test_the_doctor_group_is_wired_to_the_doctor_module() -> None:
    arguments = build_parser().parse_args(["doctor"])
    assert arguments.run is doctor.run


def test_every_group_says_in_one_line_what_it_is() -> None:
    assert set(GROUPS) == {
        "gateway",
        "worker",
        "sessions",
        "chat",
        "orgs",
        "routes",
        "keys",
        "migrate",
        "doctor",
    }
    assert all(purpose and "\n" not in purpose for purpose in GROUPS.values())
