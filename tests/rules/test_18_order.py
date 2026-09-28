"""Rule 18: a module reads top to bottom: constants, types, classes, public functions, private."""

import ast
from pathlib import Path

from tests.rules.tree import FIXTURES, relative, source_files

KINDS = (
    "imports",
    "constants",
    "classes",
    "derived constants",
    "public functions",
    "private functions",
)


def _names_of(node: ast.stmt) -> set[str]:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {node.name}
    if isinstance(node, ast.Assign):
        return {target.id for target in node.targets if isinstance(target, ast.Name)}
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return {node.target.id}
    if isinstance(node, ast.TypeAlias):
        return {node.name.id}
    return set()


def _loaded(node: ast.AST) -> set[str]:
    # A comprehension's own variables are not the module's names.
    bound = {
        n.id
        for c in ast.walk(node)
        if isinstance(c, ast.comprehension)
        for n in ast.walk(c.target)
        if isinstance(n, ast.Name)
    }
    return {
        n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
    } - bound


def _kind_of(node: ast.stmt, local: set[str]) -> int:
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return 0
    if isinstance(node, ast.TypeAlias):
        return 1
    if isinstance(node, ast.ClassDef):
        return 2
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return 5 if node.name.startswith("_") else 4
    return 3 if _loaded(node) & local else 1


def _eager_names(node: ast.stmt) -> set[str]:
    """Names a statement evaluates when the module loads, function bodies excepted."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        # Nested defs count: a function called at load time evaluates their annotations too.
        parts: list[ast.AST | None] = []
        for fn in [
            n for n in ast.walk(node) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]:
            parts += [*fn.decorator_list, *fn.args.defaults, fn.returns]
            parts += [arg.annotation for arg in ast.walk(fn.args) if isinstance(arg, ast.arg)]
        return {
            n.id
            for part in parts
            if part is not None
            for n in ast.walk(part)
            if isinstance(n, ast.Name)
        }
    if isinstance(node, ast.ClassDef):
        found: set[str] = set()
        for statement in node.body:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found |= _eager_names(statement)
            else:
                found |= {n.id for n in ast.walk(statement) if isinstance(n, ast.Name)}
        return found | {
            n.id for base in node.bases for n in ast.walk(base) if isinstance(n, ast.Name)
        }
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def out_of_order(path: Path) -> list[str]:
    """Return every statement before one of a lower kind, unless it needed that one loaded first."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    statements = [
        node for node in tree.body if not (isinstance(node, ast.Expr) and node.lineno == 1)
    ]
    local = set[str]().union(*(_names_of(node) for node in statements))
    found: list[str] = []
    highest = 0
    defined_by_kind: dict[str, int] = {}
    # What was allowed to come late, and whatever wires it up after: the app, its routers.
    late: set[str] = set()
    for node in statements:
        kind = _kind_of(node, local)
        named = (_eager_names(node) & local) - _names_of(node)
        needs_later = any(defined_by_kind.get(name, 0) > kind for name in named) or bool(
            named & late
        )
        if kind < highest and not needs_later:
            found.append(f"{relative(path)}:{node.lineno}: {KINDS[kind]} after {KINDS[highest]}")
        elif kind < highest:
            late |= _names_of(node)
        highest = max(highest, kind)
        for name in _names_of(node):
            defined_by_kind[name] = kind
    return found


def test_every_module_reads_in_order() -> None:
    assert [line for path in source_files() for line in out_of_order(path)] == []


def test_the_rule_catches_a_constant_after_a_function_and_a_class_after_a_helper() -> None:
    assert [line.split(": ", 1)[1] for line in out_of_order(FIXTURES / "rule18/jumbled.py")] == [
        "constants after public functions",
        "classes after private functions",
    ]
