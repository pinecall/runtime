"""Rule 23: a pool's connection is autocommit, so statements that belong together say so."""

import ast
import re
from pathlib import Path

from tests.rules.tree import FIXTURES, relative, source_files

# The line right above a `pool.connection()` block whose statements are independent of each other.
MARKER = "# independent: "

# What only means something inside a transaction: autocommit ends it with the statement.
NEEDS_A_TRANSACTION = re.compile(
    r"for\s+update|for\s+share|set\s+local|pg_advisory_xact_lock", re.IGNORECASE
)

MANY = "{file}:{line}: {count} statements with no transaction; open one, or say `{marker}<why>`"
ALONE = "{file}:{line}: a lock or a SET LOCAL outside a transaction ends with its own statement"


def offences(paths: list[Path]) -> list[str]:
    """Return every block that runs statements together outside a transaction."""
    texts = _constants(paths)
    found: list[str] = []
    for path in paths:
        source = path.read_text(encoding="utf-8")
        lines = source.splitlines()
        tree = ast.parse(source)
        for block, name in _connection_blocks(tree):
            loose = _loose_statements(block.body, name)
            above = lines[block.lineno - 2].strip() if block.lineno > 1 else ""
            marked = above.startswith(MARKER) and len(above) > len(MARKER)
            if _count(loose) > 1 and not _in_a_transaction(block, name) and not marked:
                found.append(
                    MANY.format(
                        file=relative(path), line=block.lineno, count=_count(loose), marker=MARKER
                    )
                )
            if not _in_a_transaction(block, name) and any(
                NEEDS_A_TRANSACTION.search(texts.get(_named(call, path), "")) for call, _ in loose
            ):
                found.append(ALONE.format(file=relative(path), line=block.lineno))
    return found


def _constants(paths: list[Path]) -> dict[str, str]:
    """Every module-level string by `module.NAME`, and by `path:NAME` where written or imported."""
    trees = {path: ast.parse(path.read_text(encoding="utf-8")) for path in paths}
    texts: dict[str, str] = {}
    for path, tree in trees.items():
        for name, text in _strings_of(tree):
            texts[f"{path.stem}.{name}"] = texts[f"{path}:{name}"] = text
    for path, tree in trees.items():
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module:
                stem = node.module.rsplit(".", 1)[-1]
                for alias in node.names:
                    if f"{stem}.{alias.name}" in texts:
                        texts[f"{path}:{alias.asname or alias.name}"] = texts[
                            f"{stem}.{alias.name}"
                        ]
    return texts


def _strings_of(tree: ast.Module) -> list[tuple[str, str]]:
    return [
        (target.id, node.value.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
        for target in node.targets
        if isinstance(target, ast.Name)
    ]


def _named(call: ast.Call, path: Path) -> str:
    if not call.args:
        return ""
    first = call.args[0]
    if isinstance(first, ast.Name):
        return f"{path}:{first.id}"
    if isinstance(first, ast.Attribute) and isinstance(first.value, ast.Name):
        return f"{first.value.id}.{first.attr}"
    return ""


def _connection_blocks(tree: ast.AST) -> list[tuple[ast.AsyncWith, str]]:
    blocks: list[tuple[ast.AsyncWith, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncWith):
            continue
        for item in node.items:
            call = item.context_expr
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "connection"
                and isinstance(item.optional_vars, ast.Name)
            ):
                blocks.append((node, item.optional_vars.id))
    return blocks


def _in_a_transaction(block: ast.AsyncWith, name: str) -> bool:
    return any(_opens_a_transaction(item.context_expr, name) for item in block.items)


def _opens_a_transaction(expression: ast.expr, name: str) -> bool:
    return (
        isinstance(expression, ast.Call)
        and isinstance(expression.func, ast.Attribute)
        and expression.func.attr == "transaction"
        and isinstance(expression.func.value, ast.Name)
        and expression.func.value.id == name
    )


# A statement is a call on the connection or one handed the connection; one inside a loop counts
# twice, and one inside a nested `connection.transaction()` is in a transaction.
def _loose_statements(body: list[ast.stmt], name: str) -> list[tuple[ast.Call, int]]:
    found: list[tuple[ast.Call, int]] = []

    def visit(node: ast.AST, weight: int) -> None:
        if isinstance(node, ast.AsyncWith) and any(
            _opens_a_transaction(item.context_expr, name) for item in node.items
        ):
            return
        if isinstance(node, ast.Call) and _on_the_connection(node, name):
            found.append((node, weight))
        looping = isinstance(
            node, ast.For | ast.AsyncFor | ast.While | ast.ListComp | ast.SetComp | ast.GeneratorExp
        )
        for child in ast.iter_child_nodes(node):
            visit(child, 2 if looping else weight)

    for statement in body:
        visit(statement, 1)
    return found


def _on_the_connection(call: ast.Call, name: str) -> bool:
    function = call.func
    if (
        isinstance(function, ast.Attribute)
        and isinstance(function.value, ast.Name)
        and function.value.id == name
        and function.attr in ("execute", "executemany", "cursor")
    ):
        return True
    return any(isinstance(argument, ast.Name) and argument.id == name for argument in call.args)


def _count(statements: list[tuple[ast.Call, int]]) -> int:
    return sum(weight for _, weight in statements)


def test_every_block_of_statements_that_belong_together_is_a_transaction() -> None:
    assert offences(source_files()) == []


def test_the_rule_catches_statements_together_and_a_lock_alone_but_not_a_marked_block() -> None:
    fixture = FIXTURES / "rule23/blocks.py"
    many = MANY.replace("{file}", relative(fixture)).replace("{marker}", MARKER)
    assert offences([fixture]) == [
        many.format(line=27, count=2),
        ALONE.format(file=relative(fixture), line=33),
        many.format(line=38, count=2),
    ]
