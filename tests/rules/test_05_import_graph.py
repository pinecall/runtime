"""Rule 5: the import graph is a list of edges; a new edge is a diff on this file."""

import ast
import sys
from pathlib import Path

from tests.rules.tree import FIXTURES, PACKAGE, package_of, relative, source_files

# What each package of pinecall/ may import of ours. Its own modules are always allowed.
EDGES: dict[str, frozenset[str]] = {
    "domain": frozenset(),
    "postgres": frozenset({"domain"}),
    "wire": frozenset({"domain"}),
    "log": frozenset({"domain", "wire", "postgres"}),
    "tenancy": frozenset({"domain", "wire", "postgres", "log"}),
    "providers": frozenset({"domain", "wire"}),
    "session": frozenset({"domain", "wire", "providers", "log"}),
    "retrieval": frozenset({"domain", "wire", "postgres", "log", "providers"}),
    "evals": frozenset({"domain", "wire", "postgres", "session", "retrieval", "log", "providers"}),
    "channels": frozenset({"domain", "wire", "postgres", "tenancy", "log"}),
    "fleet": frozenset({"domain", "wire", "log"}),
    "gateway": frozenset(
        {
            "domain",
            "wire",
            "postgres",
            "log",
            "tenancy",
            "providers",
            "session",
            "retrieval",
            "evals",
            "channels",
            "fleet",
        }
    ),
    "worker": frozenset({"domain", "wire", "session", "providers", "fleet", "channels", "log"}),
    "cli": frozenset(
        {
            "domain",
            "wire",
            "postgres",
            "log",
            "tenancy",
            "providers",
            "session",
            "retrieval",
            "evals",
            "channels",
            "fleet",
            "gateway",
            "worker",
        }
    ),
}

# The doors are the gateway's alone.
NOBODY_IMPORTS = "gateway.api"

# The leaves hold data and no framework: what each may import beyond the standard library.
LIBRARIES_OF_THE_LEAVES: dict[str, frozenset[str]] = {
    "domain": frozenset({"pydantic", "dotenv"}),
    "wire": frozenset({"pydantic"}),
}


def imports_of(module: Path, package: Path = PACKAGE) -> list[tuple[str, str]]:
    """Return (importer package, imported dotted name) for every import of ours in the module."""
    tree = ast.parse(module.read_text(encoding="utf-8"))
    importer = package_of(module, package)
    named: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("pinecall."):
            named.append(node.module.removeprefix("pinecall."))
        if isinstance(node, ast.Import):
            named += [
                alias.name.removeprefix("pinecall.")
                for alias in node.names
                if alias.name.startswith("pinecall.")
            ]
    return [(importer, name) for name in named]


def libraries_of(module: Path) -> set[str]:
    """Return the top-level names a module imports beyond ours and the standard library."""
    tree = ast.parse(module.read_text(encoding="utf-8"))
    named: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            named.add(node.module.split(".")[0])
        if isinstance(node, ast.Import):
            named |= {alias.name.split(".")[0] for alias in node.names}
    return {one for one in named if one != "pinecall" and one not in sys.stdlib_module_names}


def offences(module: Path, package: Path = PACKAGE) -> list[str]:
    found: list[str] = []
    importer = package_of(module, package)
    if importer in LIBRARIES_OF_THE_LEAVES:
        found += [
            f"{importer} imports {library}: a leaf holds data and no framework"
            for library in sorted(libraries_of(module) - LIBRARIES_OF_THE_LEAVES[importer])
        ]
    for importer, imported in imports_of(module, package):
        target = imported.split(".")[0]
        if imported.startswith(NOBODY_IMPORTS) and importer != "gateway":
            found.append(f"{importer} imports {imported}: nothing imports gateway/api/")
        elif target != importer and target not in EDGES.get(importer, frozenset()):
            found.append(f"{importer} imports {imported}: not an edge of the graph")
    return found


def test_every_import_of_ours_is_an_edge_of_the_list() -> None:
    assert [f"{relative(path)}: {one}" for path in source_files() for one in offences(path)] == []


def test_every_package_of_the_tree_has_a_row_and_no_row_names_a_package_twice() -> None:
    for importer, imported in EDGES.items():
        assert importer not in imported
        assert imported <= set(EDGES)


def test_the_rule_catches_a_leaf_importing_the_gateway_and_anybody_importing_its_doors() -> None:
    root = FIXTURES / "rule05/pinecall"
    found = offences(root / "settings/settings.py", root)
    assert len(found) == 2
    assert "not an edge" in found[0]
    assert "nothing imports gateway/api/" in found[1]


def test_the_rule_catches_a_leaf_importing_a_framework() -> None:
    root = FIXTURES / "rule05/pinecall"
    assert offences(root / "wire/rest.py", root) == [
        "wire imports fastapi: a leaf holds data and no framework"
    ]
