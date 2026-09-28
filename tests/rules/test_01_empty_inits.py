"""Rule 1: no __init__.py has content; every import names the module."""

from pathlib import Path

from tests.rules.tree import FIXTURES, PACKAGE, relative


def inits_with_content(package: Path) -> list[str]:
    return [str(path) for path in sorted(package.rglob("__init__.py")) if path.stat().st_size]


def test_every_init_of_the_package_is_empty() -> None:
    assert [relative(Path(item)) for item in inits_with_content(PACKAGE)] == []


def test_the_rule_catches_an_init_with_content() -> None:
    assert inits_with_content(FIXTURES / "rule01") == [str(FIXTURES / "rule01/pkg/__init__.py")]
