"""Rule 15: no module of the package is over 700 lines; what grows past it is two concerns."""

from pathlib import Path

from tests.rules.tree import FIXTURES, relative, source_files

CEILING = 700


def too_long(paths: list[Path], ceiling: int = CEILING) -> list[str]:
    """Return every module with more lines than the ceiling, with its count."""
    found: list[str] = []
    for path in paths:
        lines = len(path.read_text(encoding="utf-8").splitlines())
        if lines > ceiling:
            found.append(f"{relative(path)}: {lines} lines")
    return found


def test_no_module_of_the_package_is_over_the_ceiling() -> None:
    assert too_long(source_files()) == []


def test_the_rule_catches_a_module_over_the_ceiling() -> None:
    assert too_long([FIXTURES / "rule15/long.py"], ceiling=3) == [
        "tests/rules/fixtures/rule15/long.py: 5 lines"
    ]
