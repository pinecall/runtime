"""A port and its adapters, one convention everywhere: the store names the file, SQL stays by it."""

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

from pinecall_testkit.tree import ROOT, SOURCE_ROOTS

pytestmark = pytest.mark.unit

# The convention, as ARCHITECTURE.md §9 states it: `<port>.py` holds the Protocol, its records and
# its errors, and picks between the adapters in `<port>_for`; `<port>_memory.py` is the in-memory
# adapter, the port's spec by example and what the unit ring runs on; `<port>_postgres.py` is the
# Postgres one, with its statements in `<port>_sql.py` when they outgrow it. A port big enough to be
# a package keeps the same three names inside it: `protocol.py`, `memory.py`, `postgres.py`.
MEMORY_FILES = ("memory",)
POSTGRES_FILES = ("postgres",)

# SQL lives where the driver's adapter does, and in the database's own package.
SQL_MAY_LIVE_IN = ("_postgres", "_sql")
THE_DATABASE = "db"
SQL_OPENS_WITH = ("select ", "insert ", "update ", "delete ", "with ", "create ", "alter ")
SQL_SAYS = (" from ", " into ", " set ", " table ")


# A class named for a store with no twin in the other is not an adapter, and each says why here:
# a new one fails the test below until somebody decides which of the two it is.
NOT_ADAPTERS = {
    "MemoryPolicy": "a shape of contact memory (types/knowledge.py), not a store",
    "PostgresIndex": "the call index half of PostgresStore; MemoryStore is its twin, whole",
}


def classes_named_for_a_store() -> Iterator[tuple[Path, ast.ClassDef]]:
    """Every class named Memory<X> or Postgres<X>, and not an error."""
    for root in SOURCE_ROOTS:
        for path in sorted(root.rglob("*.py")):
            for node in ast.parse(path.read_text(encoding="utf-8")).body:
                if not isinstance(node, ast.ClassDef) or _is_an_error(node):
                    continue
                if _named_for("Memory", node.name) or _named_for("Postgres", node.name):
                    yield path, node


def adapters() -> list[tuple[Path, ast.ClassDef]]:
    """The classes named for a store that have a twin in the other: the port's two adapters."""
    named = list(classes_named_for_a_store())
    ports = {_port_of(node.name) for _, node in named}
    twinned = {
        port for port in ports if {f"Memory{port}", f"Postgres{port}"} <= {n.name for _, n in named}
    }
    return [(path, node) for path, node in named if _port_of(node.name) in twinned]


def test_an_adapter_lives_in_the_file_named_by_its_store() -> None:
    misplaced = [
        f"{path.relative_to(ROOT)}:{node.name}"
        for path, node in adapters()
        if not _in_its_file(path.stem, node.name)
    ]
    assert not misplaced, f"an adapter outside the file its store names: {misplaced}"


def test_a_class_named_for_a_store_is_an_adapter_or_says_why_not() -> None:
    twinned = {node.name for _, node in adapters()}
    single = {node.name for _, node in classes_named_for_a_store()} - twinned
    assert single == set(NOT_ADAPTERS), (
        f"named for a store with no twin: {sorted(single - set(NOT_ADAPTERS))}; "
        f"no longer there: {sorted(set(NOT_ADAPTERS) - single)}"
    )


def test_the_convention_is_what_the_tree_does() -> None:
    """Not vacuous: the tree has enough pairs for the rule above to hold over something."""
    assert len(adapters()) >= 2 * 15


def test_sql_is_written_only_beside_the_driver() -> None:
    """A statement in a port, a door or a verb is a second place the schema has to be read from."""
    outside: list[str] = []
    for root in SOURCE_ROOTS:
        for path in sorted(root.rglob("*.py")):
            if _may_hold_sql(path, root):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.Assign, ast.AnnAssign)) and _is_sql(node.value):
                    outside.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not outside, f"SQL outside a *_postgres.py, *_sql.py or db/: {outside}"


def _named_for(store: str, name: str) -> bool:
    """`MemoryOrgs`, `PostgresKeys` — and not `Memory` itself, contact memory's own port."""
    return name.startswith(store) and len(name) > len(store) and name[len(store)].isupper()


def _port_of(name: str) -> str:
    return (
        name.removeprefix("Memory") if name.startswith("Memory") else name.removeprefix("Postgres")
    )


def _in_its_file(stem: str, name: str) -> bool:
    store = "memory" if name.startswith("Memory") else "postgres"
    return stem == store or stem.endswith(f"_{store}")


def _is_an_error(node: ast.ClassDef) -> bool:
    """`PostgresRefused` is what a verb says, not a store: an exception class is not an adapter."""
    return any(
        isinstance(base, ast.Name) and base.id.endswith(("Error", "Refused", "Exception"))
        for base in node.bases
    )


def _may_hold_sql(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    return (
        relative.parts[0] == THE_DATABASE
        or path.stem.endswith(SQL_MAY_LIVE_IN)
        or (path.stem in POSTGRES_FILES)
    )


def _is_sql(value: ast.expr | None) -> bool:
    """A string constant that reads as a statement: it opens with a verb and names a table."""
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        said = value.value
    elif isinstance(value, ast.JoinedStr):
        said = "".join(
            part.value
            for part in value.values
            if isinstance(part, ast.Constant) and isinstance(part.value, str)
        )
    else:
        return False
    lowered = " ".join(said.lower().split())
    return lowered.startswith(SQL_OPENS_WITH) and any(word in lowered for word in SQL_SAYS)
