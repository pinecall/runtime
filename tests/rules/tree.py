"""What the rules read: the repository's roots and the files each rule checks."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "pinecall"
TESTS = ROOT / "tests"
RULES = TESTS / "rules"
FIXTURES = RULES / "fixtures"
ALLOWED = RULES / "allowed.py"
V1 = ROOT.parent / "runtime"

# Internal to the rewrite and kept out of the history; a clean clone has neither.
TREE_MD = ROOT / "TREE.md"
PARITY_MD = ROOT / "PARITY.md"


def source_files(package: Path = PACKAGE) -> list[Path]:
    """Return every module of the package, in path order."""
    return sorted(package.rglob("*.py"))


def checked_files() -> list[Path]:
    """Return every module of the package and the suites but the fixtures and the allowed list."""
    return [
        path
        for path in [*source_files(), *sorted(TESTS.rglob("*.py"))]
        if FIXTURES not in path.parents and path != ALLOWED
    ]


def package_of(module: Path, package: Path = PACKAGE) -> str:
    """Return the folder a module belongs to under pinecall/, or its own stem at the root."""
    relative = module.relative_to(package)
    return relative.parts[0] if len(relative.parts) > 1 else relative.stem


def relative(path: Path) -> str:
    """Return the path as the rules and allowed.py spell it: from the repository root."""
    return str(path.relative_to(ROOT))
