"""Rule 10: ruff, pyright strict and deptry clean, and every docstring one line."""

import ast
import subprocess
from pathlib import Path

from tests.rules.tree import FIXTURES, ROOT, checked_files


def tool(*command: str, cwd: Path = ROOT) -> tuple[int, str]:
    ran = subprocess.run(
        ["uv", "run", *command], cwd=cwd, capture_output=True, text=True, check=False
    )
    return ran.returncode, ran.stdout + ran.stderr


def long_docstrings(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    nodes = [tree, *ast.walk(tree)]
    for node in nodes:
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        docstring = ast.get_docstring(node, clean=False)
        if docstring is not None and "\n" in docstring:
            where = "module" if isinstance(node, ast.Module) else node.name
            found.append(f"{path}: {where}")
    return found


def test_ruff_finds_nothing_to_lint_or_format() -> None:
    status, said = tool("ruff", "check", ".")
    assert status == 0, said
    status, said = tool("ruff", "format", "--check", ".")
    assert status == 0, said


def test_pyright_strict_finds_nothing() -> None:
    status, said = tool("pyright")
    assert status == 0, said


def test_deptry_finds_every_import_declared_and_every_declaration_imported() -> None:
    status, said = tool("deptry", ".")
    assert status == 0, said


def test_every_docstring_is_one_line() -> None:
    assert [one for path in checked_files() for one in long_docstrings(path)] == []


def test_the_rule_catches_what_each_tool_refuses() -> None:
    fixtures = FIXTURES / "rule10"
    assert tool("ruff", "check", "--isolated", str(fixtures / "ruff.py"))[0] != 0
    assert tool("pyright", cwd=fixtures)[0] != 0
    assert tool("deptry", ".", cwd=fixtures / "deptry")[0] != 0
    assert long_docstrings(fixtures / "docstring.py") == [f"{fixtures / 'docstring.py'}: long"]
