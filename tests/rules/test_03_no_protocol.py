"""Rule 3: none of the four typing escapes outside the allowed list."""

import re
from pathlib import Path

from tests.rules.allowed import PROTOCOLS_AND_CASTS
from tests.rules.tree import FIXTURES, checked_files, relative

# Each literal is built at import so this file does not match itself.
TOKENS = tuple(
    re.compile(rf"\b{token}")
    for token in ("Proto" + r"col\b", "AB" + r"C\b", "ca" + r"st\(", "TYPE_" + r"CHECKING\b")
)


def occurrences(path: Path) -> list[tuple[str, str]]:
    text = path.read_text(encoding="utf-8")
    return [(relative(path), token.pattern) for token in TOKENS if token.search(text)]


def test_the_package_and_the_suites_name_no_protocol_abc_cast_or_type_checking() -> None:
    allowed = {(item.file, item.text) for item in PROTOCOLS_AND_CASTS}
    found = [
        occurrence
        for path in checked_files()
        for occurrence in occurrences(path)
        if occurrence not in allowed
    ]
    assert found == []


def test_the_rule_catches_all_four() -> None:
    assert len(occurrences(FIXTURES / "rule03/protocol.py")) == len(TOKENS)
