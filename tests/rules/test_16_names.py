"""Rule 16: no literary name; a name is the noun of what it holds, or the verb of what it does."""

import ast
import re
from pathlib import Path

from tests.rules.tree import FIXTURES, TESTS, relative, source_files

# Words that read as prose and say nothing about what the value is.
BANNED = frozenset(
    {
        "said",
        "held",
        "told",
        "one",
        "looked",
        "asked",
        "stood",
        "stands",
        "standing",
        "keyed",
        "whose",
    }
)
PROSE_PREFIX = re.compile(r"^_?the_")
PROSE_CLASS = re.compile(r"(Asked|Told|Looked|Wanted|Standing)$")


def _is_literary(name: str) -> bool:
    bare = name.lstrip("_")
    return bare in BANNED or PROSE_PREFIX.search(name) is not None


def literary_names(path: Path) -> list[str]:
    """Return every literary name a module binds: functions, classes, arguments, variables."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    # A class body's annotated names are data fields: the wire's and the tables' words.
    fields = {
        id(statement.target)
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
        for statement in node.body
        if isinstance(statement, ast.AnnAssign)
    }
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and PROSE_CLASS.search(node.name):
            found.append(f"{relative(path)}:{node.lineno}: class {node.name}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # A test's name is a sentence, and may say "the".
            if _is_literary(node.name) and not node.name.startswith("test_"):
                found.append(f"{relative(path)}:{node.lineno}: def {node.name}")
            args = node.args
            found += [
                f"{relative(path)}:{arg.lineno}: argument {arg.arg}"
                for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]
                if _is_literary(arg.arg)
            ]
        elif (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Store)
            and id(node) not in fields
            and _is_literary(node.id)
        ):
            found.append(f"{relative(path)}:{node.lineno}: {node.id}")
        elif (
            isinstance(node, ast.Attribute)
            and isinstance(node.ctx, ast.Store)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
            and _is_literary(node.attr)
        ):
            found.append(f"{relative(path)}:{node.lineno}: self.{node.attr}")
    return found


def test_nothing_in_the_package_or_the_suites_is_named_like_prose() -> None:
    suites = [path for path in TESTS.rglob("*.py") if "fixtures" not in path.parts]
    assert [line for path in [*source_files(), *suites] for line in literary_names(path)] == []


def test_the_rule_catches_a_participle_class_a_prose_door_and_prose_variables() -> None:
    found = literary_names(FIXTURES / "rule16/literary.py")
    assert [line.split(": ", 1)[1] for line in found] == [
        "class LogAsked",
        "def the_orgs_floor",
        "argument said",
        "argument held",
        "one",
    ]
