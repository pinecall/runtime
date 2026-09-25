"""The dispatcher: no group prints them all, and an unknown group or verb is a usage error."""

import pytest

from pinecall._exceptions import PinecallError
from pinecall.cli import GROUPS, build_parser, gateway, main
from pinecall.cli.box import verbs as box
from pinecall.cli.doctor import verbs as doctor
from pinecall.cli.fleet import verbs as fleet
from pinecall.cli.keys import verbs as keys
from pinecall.cli.orgs import verbs as orgs
from pinecall.cli.routes import verbs as routes
from pinecall.cli.sandbox import verbs as sandbox
from pinecall.cli.sessions import verbs as sessions
from pinecall.types import QUOTAS

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


# Every group that has verbs answers a bare `pinecall-runtime <group>` the same way: it says what
# there is and exits 0, the way a help screen does. `box` exited 2 on it, so a person listing the
# box's verbs read a failure and a script stopped (2026-09-20).
@pytest.mark.parametrize(
    ("group", "verbs"),
    [
        ("sessions", sessions.VERBS),
        ("orgs", orgs.VERBS),
        ("routes", routes.VERBS),
        ("keys", keys.VERBS),
        ("fleet", fleet.VERBS),
        ("box", box.VERBS),
        ("sandbox", sandbox.VERBS),
    ],
)
def test_a_group_with_no_verb_prints_its_verbs_and_exits_zero(
    group: str, verbs: tuple[str, ...], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([group]) == 0
    printed = capsys.readouterr().out
    assert all(verb in printed for verb in verbs)


@pytest.mark.parametrize(
    ("verb", "runs"),
    [("list", sessions.run_list), ("show", sessions.run_show), ("tail", sessions.run_tail)],
)
def test_every_sessions_verb_is_wired_to_its_own_function(verb: str, runs: object) -> None:
    arguments = build_parser().parse_args(["sessions", verb, *(["CA_1"] if verb == "show" else [])])
    assert arguments.run is runs


def test_the_gateway_group_is_wired_to_the_server_and_leaves_the_address_to_its_url() -> None:
    arguments = build_parser().parse_args(["gateway"])
    assert arguments.run is gateway.run
    assert (arguments.host, arguments.port) == (None, None)


def test_the_doctor_group_is_wired_to_the_doctor_module() -> None:
    arguments = build_parser().parse_args(["doctor"])
    assert arguments.run is doctor.run


def test_every_group_says_in_one_line_what_it_is() -> None:
    assert set(GROUPS) == {
        "init",
        "gateway",
        "worker",
        "sessions",
        "orgs",
        "routes",
        "keys",
        "providers",
        "fleet",
        "migrate",
        "doctor",
        "box",
        "sandbox",
    }
    assert all(purpose and "\n" not in purpose for purpose in GROUPS.values())


def test_the_quota_verb_has_one_flag_per_quota_and_the_flags_are_the_wires_names() -> None:
    """`orgs quota` sets the whole set, so a quota nobody typed a flag for could never be set."""
    parsed = build_parser().parse_args(
        ["orgs", "quota", "clinica", "--memory-facts", "0", "--knowledge-chunks", "5000"]
    )
    assert (parsed.memory_facts, parsed.knowledge_chunks) == (0, 5000)
    assert all(hasattr(parsed, name) for name in QUOTAS)


def test_keys_issue_takes_the_fleet_scope_the_worker_key_unit_types() -> None:
    """`--scope fleet` is minted here and nowhere else: infra/box/pinecall-worker-key@.service."""
    typed = ["keys", "issue", "--org", "default", "--scope", "fleet", "--scope", "app"]
    assert build_parser().parse_args(typed).scope == ["fleet", "app"]


# Anything this runtime raises DELIBERATELY is a refusal a person acts on, and a person reading a
# terminal should never be handed a stack trace out of the middle of a library for one. A verb
# that wants its own exit code still catches its own first; this is the door behind all of them.
def test_a_refusal_from_a_verb_is_one_sentence_on_stderr_and_exits_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def refuse(_arguments: object) -> int:
        raise PinecallError("the embedder has no API key in this process: set PERPLEXITY_API_KEY")

    monkeypatch.setattr(doctor, "run", refuse)

    assert main(["doctor"]) == 1

    said = capsys.readouterr()
    assert said.err.strip() == "the embedder has no API key in this process: set PERPLEXITY_API_KEY"
    assert "Traceback" not in said.err
