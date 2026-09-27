"""Rule 12: every command has a handler and every event type written is in the wire's registry."""

import ast
from pathlib import Path

import pytest

from pinecall.wire.commands import COMMANDS
from pinecall.wire.events import EVENTS
from tests.rules.tree import FIXTURES, PARITY_MD, source_files

# The handler table of gateway/api/agents.py, read from it once it exists.
HANDLED: frozenset[str] = frozenset()


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
    return [one for path in paths for one in appended_types(path) if one not in EVENTS]


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
