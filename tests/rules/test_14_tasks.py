"""Rule 14: every task has an owner; `create_task` appears only where allowed.py names one."""

import re
from pathlib import Path

from tests.rules.allowed import TASK_OWNERS
from tests.rules.tree import FIXTURES, relative, source_files

CREATE_TASK = re.compile(r"\basyncio\.create_task\(|\bloop\.create_task\(")


def unowned(paths: list[Path], owners: frozenset[str]) -> list[str]:
    """Return every file that creates a task without an owner named for it."""
    return [
        relative(path)
        for path in paths
        if CREATE_TASK.search(path.read_text(encoding="utf-8")) and relative(path) not in owners
    ]


def test_every_task_of_the_package_has_a_named_owner() -> None:
    assert unowned(source_files(), frozenset(owner.file for owner in TASK_OWNERS)) == []


def test_the_rule_catches_a_task_nobody_owns() -> None:
    assert unowned([FIXTURES / "rule14/orphan.py"], frozenset()) == [
        "tests/rules/fixtures/rule14/orphan.py"
    ]
