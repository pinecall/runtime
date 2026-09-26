"""The layout rules, checked against the tree itself: where code sits, how long, how it opens."""

from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pinecall_testkit.ring0 import SEARCH_S
from pinecall_testkit.tree import ROOT, every_module, tracked_files

pytestmark = pytest.mark.unit

LINE_CEILING = 400

# The ceiling judges what a person writes and another person reads. A lockfile is a resolver's;
# a price table is a script's (scripts/refresh-prices, a thousand models off an open source); and
# a changelog is a LEDGER — it does not become two files, it becomes one file with history missing,
# and "trim the oldest entries to fit" is the one thing a changelog must never do.
FILES_THE_CEILING_SKIPS = frozenset(
    {
        Path("uv.lock"),
        Path("CHANGELOG.md"),
        Path("packages/pinecall-providers/src/pinecall/providers/published_prices.json"),
        # Two walkthroughs whose content IS the terminal output of every step, in order. Cutting
        # one to fit would mean cutting steps, and a walkthrough with a step missing is worse
        # than none — a person following it stops at a command that does not work.
        Path("docs/from-zero.md"),
        Path("docs/a-box-in-production.md"),
        # The box's own page: a file-by-file account of a machine, its two names, its roles and
        # the traps each one cost. It grows when the box does, and a box described in two pages is
        # a box half-described in each.
        Path("infra/box/README.md"),
        # The two tables every other file reads FROM: every variable of the runtime, once, and
        # every dependency a door takes, once. They sat at exactly 400 and the next setting was
        # going to cost a split of one of them — which buys a second place to look for a variable,
        # the one thing these two files exist to prevent. They grow by rows, not by ideas.
        Path("packages/pinecall-settings/src/pinecall/settings/schema.py"),
        Path("packages/pinecall-runtime/src/pinecall/api/deps.py"),
    }
)


def test_no_python_file_sits_at_the_root() -> None:
    """The root holds the workspace, its tools and scripts; code lives under packages/."""
    stray = sorted(path.name for path in ROOT.glob("*.py"))
    assert not stray, f"Python at the repo root: {stray}"


@pytest.mark.timeout(SEARCH_S)
def test_no_tracked_file_is_longer_than_the_ceiling() -> None:
    """400 lines is the ceiling and 150 the norm: a file past it is asking to be two files."""
    too_long = {
        str(path): lines
        for path in tracked_files()
        if _the_ceiling_judges(path) and (lines := _line_count(path)) > LINE_CEILING
    }
    assert not too_long, f"over {LINE_CEILING} lines: {too_long}"


@pytest.mark.timeout(SEARCH_S)
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


@pytest.mark.timeout(SEARCH_S)
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


# A line ceiling is about what a person reads, and a PNG has no lines: counting its bytes for
# newlines said `docs/images/evals.png: 994` and failed the suite over a screenshot. Anything with
# a NUL byte in the first few kilobytes is not text — the same rule `grep` and `git` use.
def _is_text(path: Path) -> bool:
    """Whether this file is something a person reads in lines."""
    try:
        return b"\0" not in (ROOT / path).read_bytes()[:8192]
    except OSError:
        return False


def _the_ceiling_judges(path: Path) -> bool:
    """Whether the 400-line rule speaks about this file at all."""
    if not _is_text(path):
        return False
    return path not in FILES_THE_CEILING_SKIPS


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
    return any(longer[:cut] + longer[cut + 1 :] == shorter for cut in range(len(longer)))


A_NAME = st.text(alphabet="ab_", max_size=6)


# The rule reads the same whichever file is named first, and a name is never its own twin.
@pytest.mark.timeout(SEARCH_S)
@given(one=A_NAME, other=A_NAME)
def test_one_edit_apart_is_symmetric_and_never_holds_between_a_name_and_itself(
    one: str, other: str
) -> None:
    assert _one_edit_apart(one, other) == _one_edit_apart(other, one)
    assert not _one_edit_apart(one, one)
