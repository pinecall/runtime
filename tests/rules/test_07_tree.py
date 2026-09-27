"""Rule 7: TREE.md is the law; every file under pinecall/ is a line of it."""

from pathlib import Path

import pytest

from tests.rules.tree import FIXTURES, PACKAGE, TREE_MD


def listed_in(tree_md: Path) -> set[str]:
    """Return the paths TREE.md lists: the first word of each line that starts with pinecall/."""
    return {
        line.split()[0]
        for line in tree_md.read_text(encoding="utf-8").splitlines()
        if line.startswith("pinecall/")
    }


def on_disk(package: Path) -> set[str]:
    root = package.parent
    return {
        str(path.relative_to(root))
        for path in package.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }


def unlisted(package: Path, tree_md: Path) -> set[str]:
    return on_disk(package) - listed_in(tree_md)


# A listed file may not exist yet; an unlisted one may not exist at all.
@pytest.mark.skipif(not TREE_MD.is_file(), reason="TREE.md is internal: a clean clone has none")
def test_every_file_under_the_package_is_a_line_of_the_tree() -> None:
    assert unlisted(PACKAGE, TREE_MD) == set()


def test_the_rule_catches_a_file_the_tree_does_not_name() -> None:
    root = FIXTURES / "rule07"
    assert unlisted(root / "pinecall", root / "TREE.md") == {"pinecall/unlisted.py"}
