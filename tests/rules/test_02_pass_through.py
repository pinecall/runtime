"""Rule 2: no pass-through function, and no method that only forwards to an attribute."""

import ast
from pathlib import Path

from tests.rules.tree import FIXTURES, relative, source_files


def pass_throughs(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        f"{relative(path) if path.is_relative_to(FIXTURES.parents[2]) else path}:{node.lineno} "
        f"{node.name}"
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and _forwards(node)
    ]


def _forwards(function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    body = [statement for statement in function.body if not _is_a_docstring(statement)]
    if len(body) != 1 or not isinstance(body[0], ast.Return) or body[0].value is None:
        return False
    value = body[0].value
    if isinstance(value, ast.Await):
        value = value.value
    if not isinstance(value, ast.Call):
        return False
    arguments = function.args
    names = [one.arg for one in [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]]
    if names and names[0] in ("self", "cls"):
        names = names[1:]
    passed = [one.id for one in value.args if isinstance(one, ast.Name)]
    passed += [
        one.value.id
        for one in value.keywords
        if isinstance(one.value, ast.Name) and one.arg == one.value.id
    ]
    if len(passed) != len(value.args) + len(value.keywords):
        return False
    return sorted(passed) == sorted(names)


def _is_a_docstring(statement: ast.stmt) -> bool:
    return (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and isinstance(statement.value.value, str)
    )


def test_no_function_of_the_package_only_forwards_its_own_parameters() -> None:
    assert [one for path in source_files() for one in pass_throughs(path)] == []


def test_the_rule_catches_forwarding_in_any_order_awaited_or_through_an_attribute() -> None:
    found = pass_throughs(FIXTURES / "rule02/forwarding.py")
    assert [one.rsplit(" ", 1)[1] for one in found] == ["open_call", "seal", "read"]
