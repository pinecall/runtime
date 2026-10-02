"""Rule 24: every script of infra/ parses: shell under bash -n, Python as Python."""

import ast
import subprocess
from pathlib import Path

from tests.rules.tree import FIXTURES, ROOT, relative

INFRA = ROOT / "infra"


def scripts(root: Path) -> list[Path]:
    """Every shell script (by `.sh` or a bash shebang) and every Python file under the root."""
    found: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or ".terraform" in path.parts:
            continue
        if path.suffix in (".sh", ".py") or _a_bash_shebang(path):
            found.append(path)
    return found


def broken(paths: list[Path]) -> list[str]:
    """Each script that does not parse, with the parser's first words."""
    found: list[str] = []
    for path in paths:
        if path.suffix == ".py":
            try:
                ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError as error:
                found.append(f"{path}: {error.msg}")
            continue
        checked = ("bash", "-n", str(path))
        done = subprocess.run(checked, capture_output=True, text=True, check=False)
        if done.returncode != 0:
            found.append(f"{path}: {done.stderr.strip().splitlines()[0]}")
    return found


def _a_bash_shebang(path: Path) -> bool:
    with path.open("rb") as opened:
        first = opened.readline(64)
    return first.startswith(b"#!") and b"bash" in first


def test_every_script_of_infra_parses() -> None:
    assert [relative(Path(item.split(":")[0])) for item in broken(scripts(INFRA))] == []


def test_the_rule_reads_scripts_by_suffix_and_shebang_and_catches_one_that_does_not_parse() -> None:
    found = scripts(FIXTURES / "rule24")
    assert [path.name for path in found] == ["broken.sh", "fine", "fine.py"]
    assert [item.split(":")[0] for item in broken(found)] == [str(FIXTURES / "rule24/broken.sh")]
