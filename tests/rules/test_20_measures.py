"""Rule 20: the architecture page carries each folder's measure, and the measures are today's."""

import re
from dataclasses import dataclass
from pathlib import Path

from tests.rules.test_05_import_graph import imports_of
from tests.rules.tree import FIXTURES, PACKAGE, ROOT, package_of, source_files

ARCHITECTURE = ROOT / "docs/architecture.md"

# A folder may drift this many lines before the page is refreshed.
TOLERANCE = 100

HEADING = "## The measures"

A_ROW = re.compile(r"^\| `([a-z_]+)/` \| (\d+) \| (\d+) \| (.*) \|$")

NONE = "—"


@dataclass(frozen=True)
class Measure:
    """One folder: its modules, its lines, and the folders of ours it imports."""

    folder: str
    files: int
    lines: int
    imports: str


def measured(package: Path = PACKAGE) -> list[Measure]:
    """Return every folder's measure, in the order the folders sort."""
    by_folder: dict[str, list[Path]] = {}
    for path in source_files(package):
        if path.name == "__init__.py" or path.parent == package:
            continue
        by_folder.setdefault(package_of(path, package), []).append(path)
    found: list[Measure] = []
    for folder, paths in sorted(by_folder.items()):
        imported = {
            target.split(".")[0]
            for path in paths
            for _, target in imports_of(path, package)
            if target.split(".")[0] != folder
        }
        lines = sum(len(path.read_text(encoding="utf-8").splitlines()) for path in paths)
        listed = ", ".join(f"`{name}`" for name in sorted(imported)) or NONE
        found.append(Measure(folder, len(paths), lines, listed))
    return found


def table_of(measures: list[Measure]) -> str:
    """Return the measures as the page's table."""
    rows = [
        f"| `{measure.folder}/` | {measure.files} | {measure.lines} | {measure.imports} |"
        for measure in measures
    ]
    return "\n".join(["| folder | files | lines | imports of ours |", "|---|---|---|---|", *rows])


def stated(page: Path) -> list[Measure]:
    """Return the measures the page states under its heading."""
    found: list[Measure] = []
    section = page.read_text(encoding="utf-8").split(HEADING, 1)[-1].split("\n## ", 1)[0]
    for line in section.splitlines():
        row = A_ROW.match(line.strip())
        if row is not None:
            found.append(Measure(row.group(1), int(row.group(2)), int(row.group(3)), row.group(4)))
    return found


def stale(page: list[Measure], today: list[Measure], *, tolerance: int = TOLERANCE) -> list[str]:
    """Return what the page gets wrong: a folder, its files, its imports, or its lines."""
    written = {measure.folder: measure for measure in page}
    found = [
        f"{measure.folder}/: not on the page" for measure in today if measure.folder not in written
    ]
    found += [
        f"{measure.folder}/: no such folder"
        for measure in page
        if measure.folder not in {t.folder for t in today}
    ]
    for fresh in today:
        old = written.get(fresh.folder)
        if old is None:
            continue
        if old.files != fresh.files:
            found.append(
                f"{fresh.folder}/: {old.files} files on the page, {fresh.files} in the tree"
            )
        if old.imports != fresh.imports:
            found.append(
                f"{fresh.folder}/: imports {old.imports} on the page, {fresh.imports} in the tree"
            )
        if abs(old.lines - fresh.lines) > tolerance:
            found.append(
                f"{fresh.folder}/: {old.lines} lines on the page, {fresh.lines} in the tree"
            )
    return found


def test_the_page_carries_todays_measures() -> None:
    today = measured()
    assert stale(stated(ARCHITECTURE), today) == [], f"paste under {HEADING}:\n{table_of(today)}"


def test_the_rule_catches_a_page_that_fell_behind() -> None:
    fixture = FIXTURES / "rule20"
    assert stale(stated(fixture / "architecture.md"), measured(fixture / "pinecall")) == [
        "b/: not on the page",
        "c/: no such folder",
        "a/: 2 files on the page, 1 in the tree",
        "a/: imports — on the page, `b` in the tree",
        "a/: 400 lines on the page, 5 in the tree",
    ]
