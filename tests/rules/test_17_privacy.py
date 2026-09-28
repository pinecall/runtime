"""Rule 17: a `_module` is its package's alone; a plain module is imported by another package."""

import ast
from pathlib import Path

from tests.rules.allowed import NOT_YET_REACHED
from tests.rules.tree import FIXTURES, PACKAGE

# Modules no package imports by design: the processes, the doors (their surface is the URL).
ENTRYPOINTS = ("gateway/app.py", "worker/main.py", "cli/main.py")
DOORS = "gateway/api/"


def imports_by_module(package: Path) -> dict[str, set[str]]:
    """Return, per module of the package, the dotted modules of ours it imports."""
    found: dict[str, set[str]] = {}
    for path in sorted(package.rglob("*.py")):
        if path.name == "__init__.py" or "__pycache__" in path.parts:
            continue
        me = ".".join(path.relative_to(package.parent).with_suffix("").parts)
        found[me] = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.startswith("pinecall.")
            ):
                found[me].add(node.module)
                found[me] |= {f"{node.module}.{alias.name}" for alias in node.names}
    return found


def offences(package: Path) -> list[str]:
    """Return every private module reached from outside, and every public one nobody reaches."""
    graph = imports_by_module(package)
    found: list[str] = []
    for module in graph:
        parts = module.split(".")
        name, own_package = parts[-1], ".".join(parts[:-1])
        outsiders = {
            importer
            for importer, imported in graph.items()
            if module in imported and not importer.startswith(own_package + ".")
        }
        rel = "/".join(parts[1:]) + ".py"
        if name.startswith("_") and outsiders:
            found.append(f"{rel} is private and {min(outsiders)} imports it")
        elif (
            not name.startswith("_")
            and not outsiders
            and not rel.startswith(DOORS)
            and rel not in ENTRYPOINTS
            and f"pinecall/{rel}" not in {waiting.file for waiting in NOT_YET_REACHED}
        ):
            found.append(f"{rel} is public and no other package imports it")
    return found


def test_private_modules_stay_home_and_public_ones_are_used_from_outside() -> None:
    assert offences(PACKAGE) == []


def test_the_rule_catches_a_private_module_reached_and_a_public_one_nobody_uses() -> None:
    assert offences(FIXTURES / "rule17/pinecall") == [
        "alpha/_private.py is private and pinecall.beta.user imports it",
        "alpha/lonely.py is public and no other package imports it",
        "beta/user.py is public and no other package imports it",
    ]
