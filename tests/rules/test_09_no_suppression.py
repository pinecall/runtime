"""Rule 9: no checker is silenced by a comment outside the allowed list."""

from pathlib import Path

from tests.rules.allowed import SUPPRESSIONS
from tests.rules.tree import FIXTURES, checked_files, relative

# Each literal is built at import so this file does not match itself.
MARKS = ("no" + "qa", "type: " + "ignore", "pyright: " + "ignore", "pragma: " + "no cover")


def suppressions(path: Path) -> list[tuple[str, str]]:
    text = path.read_text(encoding="utf-8")
    return [(relative(path), mark) for mark in MARKS if mark in text]


def test_nothing_in_the_package_or_the_suites_silences_a_checker() -> None:
    allowed = {(one.file, one.text) for one in SUPPRESSIONS}
    found = [one for path in checked_files() for one in suppressions(path) if one not in allowed]
    assert found == []


def test_the_rule_catches_all_four_marks() -> None:
    assert len(suppressions(FIXTURES / "rule09/suppressed.py")) == len(MARKS)
