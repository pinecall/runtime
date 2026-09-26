"""The tests mirror the source: one directory per package, both ways, in both distributions."""

from pathlib import Path

import pytest

from tests.support.tree import CORE_ROOT, CORE_TESTS_ROOT, PACKAGE_ROOT, TESTS_ROOT

pytestmark = pytest.mark.unit

# What a source tree holds that is no package of code: the pages a build copies in, and the SQL.
NOT_CODE = frozenset({"public", "migrations"})

# What a suite holds that mirrors no package: what every test leans on, and nothing else.
NOT_A_MIRROR = frozenset({"support"})

# A package with nothing to test on its own, each with why: a new one fails until it has tests.
UNTESTED = {
    (CORE_ROOT, "errors"): "PinecallError alone, the root every refusal derives from; no behaviour",
}

# A directory of tests that groups the doors of one surface rather than mirroring a subpackage,
# each with why: the doors of `api/` are modules, and a surface's tests outgrow one directory.
GROUPINGS = {
    (TESTS_ROOT, "api/agents/pipeline"): "the pipeline, tuning and hold doors, one fixture apart",
    (TESTS_ROOT, "api/calls/tokens"): "the token doors: the room's, the log's, the listen door's",
    (TESTS_ROOT, "api/ops/orgs"): "the operator's doors onto every org: quotas, keys, usage",
    (TESTS_ROOT, "providers/vendors"): "a fake vendor package the plugin tests install",
}

SUITES = ((PACKAGE_ROOT, TESTS_ROOT), (CORE_ROOT, CORE_TESTS_ROOT))


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


@pytest.mark.parametrize(("source", "tests"), SUITES)
def test_every_package_has_its_directory_of_tests(source: Path, tests: Path) -> None:
    top = {name for name in packages_under(source, NOT_CODE) if "/" not in name}
    held = packages_under(tests, NOT_A_MIRROR)
    missing = sorted(name for name in top - held if (source, name) not in UNTESTED)
    assert not missing, f"packages with no tests/<package>/: {missing}"


@pytest.mark.parametrize(("source", "tests"), SUITES)
def test_every_directory_of_tests_names_a_package(source: Path, tests: Path) -> None:
    packages = packages_under(source, NOT_CODE)
    strays = sorted(
        name
        for name in packages_under(tests, NOT_A_MIRROR)
        if name not in packages and (tests, name) not in GROUPINGS
    )
    assert not strays, f"test directories that mirror no package: {strays}"


def test_every_exception_still_names_something_that_exists() -> None:
    for root, name in [*UNTESTED, *GROUPINGS]:
        assert (root / name).is_dir(), f"{root / name} is gone: take it out of the list"
