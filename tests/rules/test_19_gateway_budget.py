"""Rule 19: the gateway's private modules hold what the process keeps between doors, and no more."""

from pathlib import Path

from tests.rules.tree import FIXTURES, PACKAGE, relative

# Together: past it, the next concern is a package with a name, not another `_module`.
WHOLE = 2000

# Each: a private module past it holds two concerns.
EACH = 600

OVER_THE_WHOLE = (
    "the gateway's private modules: {total} lines together, over {whole}: "
    "the next concern is a package with a name"
)


def private_modules(gateway: Path = PACKAGE / "gateway") -> list[Path]:
    """Return the gateway's `_modules`, in path order."""
    return sorted(gateway.glob("_*.py"))


def over_budget(paths: list[Path], *, whole: int = WHOLE, each: int = EACH) -> list[str]:
    """Return every private module over its share, and the whole when it is over its budget."""
    counted = {path: len(path.read_text(encoding="utf-8").splitlines()) for path in paths}
    found = [
        f"{relative(path)}: {lines} lines, over {each}"
        for path, lines in counted.items()
        if lines > each
    ]
    total = sum(counted.values())
    if total > whole:
        found.append(OVER_THE_WHOLE.format(total=total, whole=whole))
    return found


def test_the_gateways_private_modules_stay_within_their_budget() -> None:
    assert over_budget(private_modules()) == []


def test_the_rule_catches_a_module_over_its_share_and_a_gateway_over_the_whole() -> None:
    assert over_budget(private_modules(FIXTURES / "rule19"), whole=6, each=4) == [
        "tests/rules/fixtures/rule19/_a.py: 5 lines, over 4",
        OVER_THE_WHOLE.format(total=9, whole=6),
    ]
