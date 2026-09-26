"""The tests mirror the source: one directory per package, both ways, in every distribution."""

from pathlib import Path

import pytest

from pinecall_testkit.tree import DISTRIBUTIONS, PACKAGES

pytestmark = pytest.mark.unit

# What a source tree holds that is no package of code: the pages a build copies in, and the SQL.
# A suite skips nothing: what every suite leans on is the testkit, a distribution of its own.
NOT_CODE = frozenset({"public", "migrations"})

# A package with nothing to test on its own, each with why: a new one fails until it has tests.
UNTESTED = {
    (
        "pinecall-core",
        "errors",
    ): "PinecallError alone, the root every refusal derives from; no behaviour",
}

# A directory of tests that groups the doors of one surface rather than mirroring a subpackage,
# each with why: the doors of `api/` are modules, and a surface's tests outgrow one directory.
GROUPINGS = {
    (
        "pinecall-runtime",
        "api/agents/pipeline",
    ): "the pipeline, tuning and hold doors, one fixture apart",
    (
        "pinecall-runtime",
        "api/calls/tokens",
    ): "the token doors: the room's, the log's, the listen door's",
    (
        "pinecall-runtime",
        "api/ops/orgs",
    ): "the operator's doors onto every org: quotas, keys, usage",
    ("pinecall-providers", "providers/vendors"): "a fake vendor package the plugin tests install",
}

# Every distribution whose code is a portion of the namespace, with the suite beside it.
SUITES = [
    (d.name, d / "src" / "pinecall", d / "tests")
    for d in DISTRIBUTIONS
    if (d / "src" / "pinecall").is_dir()
]


def packages_under(root: Path, skipped: frozenset[str]) -> set[str]:
    """Every directory under root holding a module, as a path from root, but the skipped ones."""
    return {
        directory.relative_to(root).as_posix()
        for directory in root.rglob("*")
        if directory.is_dir()
        and "__pycache__" not in directory.parts
        and not skipped & set(directory.relative_to(root).parts)
        and any(child.suffix == ".py" for child in directory.iterdir())
    }


@pytest.mark.parametrize(("name", "source", "tests"), SUITES)
def test_every_package_has_its_directory_of_tests(name: str, source: Path, tests: Path) -> None:
    top = {name for name in packages_under(source, NOT_CODE) if "/" not in name}
    held = packages_under(tests, frozenset())
    missing = sorted(package for package in top - held if (name, package) not in UNTESTED)
    assert not missing, f"packages with no tests/<package>/: {missing}"


@pytest.mark.parametrize(("name", "source", "tests"), SUITES)
def test_every_directory_of_tests_names_a_package(name: str, source: Path, tests: Path) -> None:
    packages = packages_under(source, NOT_CODE)
    strays = sorted(
        directory
        for directory in packages_under(tests, frozenset())
        if directory not in packages and (name, directory) not in GROUPINGS
    )
    assert not strays, f"test directories that mirror no package: {strays}"


def test_every_exception_still_names_something_that_exists() -> None:
    for distribution, package in UNTESTED:
        where = PACKAGES / distribution / "src" / "pinecall" / package
        assert where.is_dir(), f"{where} is gone: take it out of the list"
    for distribution, directory in GROUPINGS:
        where = PACKAGES / distribution / "tests" / directory
        assert where.is_dir(), f"{where} is gone: take it out of the list"
