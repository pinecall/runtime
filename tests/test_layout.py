"""The layout rules, checked against the tree itself: where code sits, how long, how it opens."""

from pathlib import Path

import pytest

from tests.tree import ROOT, every_module, tracked_files

pytestmark = pytest.mark.unit

LINE_CEILING = 400

# The ceiling judges what a person writes and another person reads; a lockfile is a resolver's.
FILES_THE_CEILING_SKIPS = frozenset({Path("uv.lock")})
# The design corpus is imported whole and read as a book, not as code.
DIRECTORIES_THE_CEILING_SKIPS = (Path("docs/design"),)


def test_no_python_file_sits_at_the_root() -> None:
    """The root holds configuration and scripts; code lives under src/, tests under tests/."""
    stray = sorted(path.name for path in ROOT.glob("*.py"))
    assert not stray, f"Python at the repo root: {stray}"


def test_no_tracked_file_is_longer_than_the_ceiling() -> None:
    """400 lines is the ceiling and 150 the norm: a file past it is asking to be two files."""
    too_long = {
        str(path): lines
        for path in tracked_files()
        if _the_ceiling_judges(path) and (lines := _line_count(path)) > LINE_CEILING
    }
    assert not too_long, f"over {LINE_CEILING} lines: {too_long}"


def test_every_module_opens_with_a_one_line_docstring() -> None:
    """The first line says what the file is and for whom; the why goes to docs/decisions/."""
    modules = every_module()
    silent = [str(module.path) for module in modules if not module.docstring]
    assert not silent, f"no module docstring: {silent}"
    talkative = [
        str(module.path)
        for module in modules
        if module.docstring and "\n" in module.docstring.strip()
    ]
    assert not talkative, f"a module docstring runs past one line: {talkative}"


def test_no_two_modules_in_one_directory_differ_by_one_letter() -> None:
    """llm.py beside llms.py, session.py beside sessions.py: a reader cannot tell which to open."""
    by_directory: dict[Path, list[str]] = {}
    for module in every_module():
        by_directory.setdefault(module.path.parent, []).append(module.path.stem)
    twins = [
        f"{directory}/{{{one},{other}}}.py"
        for directory, names in by_directory.items()
        for one in names
        for other in names
        if one < other and _one_edit_apart(one, other)
    ]
    assert not twins, f"names one letter apart: {twins}"


def _the_ceiling_judges(path: Path) -> bool:
    """Whether the 400-line rule speaks about this file at all."""
    if path in FILES_THE_CEILING_SKIPS:
        return False
    return not any(directory in path.parents for directory in DIRECTORIES_THE_CEILING_SKIPS)


def _line_count(path: Path) -> int:
    """Lines as a reader counts them, read as bytes so a binary file cannot crash the run."""
    return (ROOT / path).read_bytes().count(b"\n")


def _one_edit_apart(one: str, other: str) -> bool:
    """True when one insertion, deletion or substitution turns one name into the other."""
    if abs(len(one) - len(other)) > 1:
        return False
    shorter, longer = sorted((one, other), key=len)
    if len(shorter) == len(longer):
        return sum(a != b for a, b in zip(shorter, longer, strict=True)) == 1
    for cut in range(len(longer)):
        if longer[:cut] + longer[cut + 1 :] == shorter:
            return True
    return False
