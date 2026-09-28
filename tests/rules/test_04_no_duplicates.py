"""Rule 4: nothing written twice; pylint's duplicate-code over the package finds nothing."""

import json
import subprocess
from pathlib import Path

from tests.rules.tree import FIXTURES, PACKAGE, ROOT

MIN_SIMILARITY_LINES = 6


def duplicates(paths: list[Path]) -> list[str]:
    ran = subprocess.run(
        [
            "uv",
            "run",
            "pylint",
            "--disable=all",
            "--enable=duplicate-code",
            f"--min-similarity-lines={MIN_SIMILARITY_LINES}",
            "--output-format=json",
            *map(str, paths),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    findings: list[dict[str, str]] = json.loads(ran.stdout or "[]")
    return [f"{finding['path']}:{finding['line']} {finding['message']}" for finding in findings]


def test_the_package_holds_no_six_lines_written_twice() -> None:
    assert duplicates([PACKAGE]) == []


def test_the_rule_catches_six_identical_lines_in_two_files() -> None:
    found = duplicates([FIXTURES / "rule04/a.py", FIXTURES / "rule04/b.py"])
    assert found
    assert "Similar lines" in found[0]
