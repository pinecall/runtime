"""Rule 8: tests mirror the source, one per module (`_b.py` mirrors `test_b.py`), no orphan."""

from pathlib import Path

from tests.rules.tree import FIXTURES, PACKAGE, TESTS

# Suites that mirror no module: the rules, the live suite, the shared fakes and configuration, and
# the suites of the scripts under infra/, which are beside the package, not in it.
NOT_A_MIRROR = ("rules", "live", "fakes", "infra", "conftest.py")


def mirror_of(module: Path, package: Path) -> Path:
    relative = module.relative_to(package)
    return package.parent / "tests" / relative.parent / f"test_{relative.name.removeprefix('_')}"


def unmirrored(package: Path) -> list[str]:
    return [
        str(module.relative_to(package.parent))
        for module in sorted(package.rglob("*.py"))
        if module.name != "__init__.py" and not mirror_of(module, package).is_file()
    ]


def orphans(package: Path, tests: Path) -> list[str]:
    found: list[str] = []
    for test in sorted(tests.rglob("test_*.py")):
        relative = test.relative_to(tests)
        if relative.parts[0] in NOT_A_MIRROR:
            continue
        name = relative.name.removeprefix("test_")
        module = package / relative.parent / name
        if not module.is_file() and not (package / relative.parent / f"_{name}").is_file():
            found.append(str(test.relative_to(package.parent)))
    return found


def test_every_module_has_its_mirror_and_every_mirror_its_module() -> None:
    assert unmirrored(PACKAGE) == []
    assert orphans(PACKAGE, TESTS) == []


def test_the_rule_catches_a_module_without_a_test_and_a_test_without_a_module() -> None:
    root = FIXTURES / "rule08"
    assert unmirrored(root / "pinecall") == ["pinecall/lonely.py"]
    assert orphans(root / "pinecall", root / "tests") == ["tests/test_orphan.py"]
