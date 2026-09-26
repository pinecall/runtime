"""The tree under test, read once: the files git tracks, and every module parsed with ast."""

import ast
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "src" / "pinecall"
TESTS_ROOT = ROOT / "tests"
# The other portion of the `pinecall` namespace: pinecall-core, a distribution of its own.
CORE = ROOT / "packages" / "pinecall-core"
CORE_ROOT = CORE / "src" / "pinecall"
CORE_TESTS_ROOT = CORE / "tests"
SOURCE_ROOTS = (PACKAGE_ROOT, CORE_ROOT)


@dataclass(frozen=True)
class PythonModule:
    """One parsed file: where it lives, how it opens, and every module its imports name."""

    path: Path
    docstring: str | None
    imported_modules: frozenset[str]

    def imports(self, package: str) -> bool:
        """True when the module names `package` itself, or anything under it."""
        prefix = f"{package}."
        return any(name == package or name.startswith(prefix) for name in self.imported_modules)


@cache
def tracked_files() -> tuple[Path, ...]:
    """Every file git tracks, relative to the repo root. Untracked scratch is nobody's business."""
    listing = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True, text=True
    )
    return tuple(Path(name) for name in listing.stdout.split("\0") if name)


@cache
def modules_under(directory: Path) -> tuple[PythonModule, ...]:
    """Every .py file below `directory`, in path order. A directory that is not there is empty."""
    return tuple(_read_module(path) for path in sorted(directory.rglob("*.py")))


def every_module() -> tuple[PythonModule, ...]:
    """Everything the two distributions own: their source, and the tests that read it."""
    return (
        modules_under(PACKAGE_ROOT)
        + modules_under(CORE_ROOT)
        + modules_under(TESTS_ROOT)
        + modules_under(CORE_TESTS_ROOT)
    )


def package_dir(name: str) -> Path:
    """Where `pinecall/<name>` lives: under the runtime's source or the core's, never both."""
    found = [root / name for root in SOURCE_ROOTS if (root / name).is_dir()]
    assert len(found) == 1, f"pinecall/{name} is in {len(found)} source roots: {found}"
    return found[0]


@cache
def _read_module(path: Path) -> PythonModule:
    """Parse one file. Cached, so the layout and isolation suites share a single parse."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return PythonModule(
        path=path.relative_to(ROOT),
        docstring=ast.get_docstring(tree),
        imported_modules=frozenset(_imported_module_names(tree, _package_of(path))),
    )


def _imported_module_names(tree: ast.Module, package: str) -> Iterator[str]:
    """Every module an import names, absolute. `from a import b` names both `a` and `a.b`."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom):
            root = _absolute_root(node, package)
            if not root:
                continue
            yield root
            for alias in node.names:
                yield f"{root}.{alias.name}"


def _absolute_root(node: ast.ImportFrom, package: str) -> str:
    """Resolve a relative import against the package the file sits in, so the name is absolute."""
    if not node.level:
        return node.module or ""
    parts = package.split(".") if package else []
    ancestor = ".".join(parts[: len(parts) - node.level + 1])
    return f"{ancestor}.{node.module}" if node.module else ancestor


def _package_of(path: Path) -> str:
    """The dotted package a file sits in — the anchor a relative import resolves against."""
    for source_root in (root.parent for root in SOURCE_ROOTS):
        if source_root in path.parents:
            return ".".join(path.parent.relative_to(source_root).parts)
    return ""
