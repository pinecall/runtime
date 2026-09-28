"""Rule 12: every command has a handler and every event type written is in the wire's registry."""

import ast
from pathlib import Path

import pytest

from pinecall.wire.commands import COMMANDS
from pinecall.wire.events import EVENTS
from tests.rules.tree import FIXTURES, PACKAGE, PARITY_MD, source_files

# A command is handled where a `case Model():` arm names its model: the app socket takes the
# agent's own, and hands the rest to the session.
HANDLERS = (
    PACKAGE / "gateway/api/apps.py",
    PACKAGE / "session/session.py",
    PACKAGE / "session/room.py",
)


def matched_classes(path: Path) -> set[str]:
    """Return every class a `case Name()` arm or an `isinstance(x, Name)` of the module names."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    arms = {
        node.pattern.cls.id
        for node in ast.walk(tree)
        if isinstance(node, ast.match_case)
        and isinstance(node.pattern, ast.MatchClass)
        and isinstance(node.pattern.cls, ast.Name)
    }
    checks = {
        name
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "isinstance"
        and len(node.args) == 2
        for name in _names_in(node.args[1])
    }
    return arms | checks


def _names_in(node: ast.expr) -> list[str]:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.BinOp):
        return _names_in(node.left) + _names_in(node.right)
    return []


def handled_commands() -> frozenset[str]:
    """Return every command type whose model a handler matches on."""
    matched = set[str]().union(*(matched_classes(path) for path in HANDLERS))
    return frozenset(name for name, model in COMMANDS.items() if model.__name__ in matched)


HANDLED = handled_commands()


def appended_types(path: Path) -> list[str]:
    """Return every literal type passed as the first argument of an `.append(...)` call."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "append"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    ]


def unknown_events(paths: list[Path]) -> list[str]:
    return [
        appended_type
        for path in paths
        for appended_type in appended_types(path)
        if appended_type not in EVENTS
    ]


def handled_per_parity() -> list[str]:
    text = PARITY_MD.read_text(encoding="utf-8")
    section = text.split("## Commands handled", 1)[1].split("\n## ", 1)[0]
    return [line[2:].strip() for line in section.splitlines() if line.startswith("- ")]


def test_every_event_type_the_package_appends_is_one_the_protocol_knows() -> None:
    assert unknown_events(source_files()) == []


@pytest.mark.skipif(not PARITY_MD.is_file(), reason="PARITY.md is internal: a clean clone has none")
def test_every_command_parity_says_is_handled_has_a_handler_and_is_a_command() -> None:
    for command in handled_per_parity():
        assert command in COMMANDS, f"{command} is not a protocol command"
        assert command in HANDLED, f"{command} is handled per PARITY.md and has no handler"


def test_the_rule_catches_an_event_the_protocol_does_not_know() -> None:
    assert unknown_events([FIXTURES / "rule12/appends.py"]) == ["call.imagined"]
