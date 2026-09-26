"""The tree under test, read once: the files git tracks, and every module parsed with ast."""

import ast
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from functools import cache
from pathlib import Path

# The repository, and every distribution of its workspace under packages/: a directory with a
# pyproject.toml, whose code is a portion of the `pinecall` namespace (src/pinecall/) or the
# testkit's own package, and whose suite is its tests/.
ROOT = Path(__file__).resolve().parents[4]
PACKAGES = ROOT / "packages"
DISTRIBUTIONS = tuple(sorted(d for d in PACKAGES.iterdir() if (d / "pyproject.toml").is_file()))
# Every portion of the namespace: what `pinecall.<package>` resolves to, one directory each.
SOURCE_ROOTS = tuple(
    d / "src" / "pinecall" for d in DISTRIBUTIONS if (d / "src" / "pinecall").is_dir()
)
TEST_ROOTS = tuple(d / "tests" for d in DISTRIBUTIONS if (d / "tests").is_dir())
TESTKIT_ROOT = PACKAGES / "pinecall-testkit" / "src" / "pinecall_testkit"
# The kernel the rules single out: the shapes and the points, on the standard library alone.
CORE_ROOT = PACKAGES / "pinecall-core" / "src" / "pinecall"


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


def source_modules() -> tuple[PythonModule, ...]:
    """Every module of every portion of the namespace: the code the distributions ship."""
    return tuple(module for root in SOURCE_ROOTS for module in modules_under(root))


def every_module() -> tuple[PythonModule, ...]:
    """Everything the workspace owns: the shipped code, the testkit, and every suite."""
    return (
        source_modules()
        + modules_under(TESTKIT_ROOT)
        + tuple(module for root in TEST_ROOTS for module in modules_under(root))
    )


def package_of(module: PythonModule) -> str:
    """The `pinecall.<package>` a shipped module belongs to: its first directory under its root."""
    for root in SOURCE_ROOTS:
        if (ROOT / module.path).is_relative_to(root):
            return (ROOT / module.path).relative_to(root).parts[0]
    raise ValueError(f"{module.path} is not shipped code")


def package_dir(name: str) -> Path:
    """Where `pinecall/<name>` lives: in exactly one distribution's source."""
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
