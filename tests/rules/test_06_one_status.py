"""Rule 6: a status is decided once; HTTPException appears in gateway/deps.py and app.py only."""

from pathlib import Path

from tests.rules.tree import FIXTURES, relative, source_files

WHERE_A_STATUS_IS_ANSWERED = ("pinecall/gateway/deps.py", "pinecall/gateway/app.py")


def deciding_a_status(paths: list[Path]) -> list[str]:
    return [str(path) for path in paths if "HTTPException" in path.read_text(encoding="utf-8")]


def test_no_door_or_domain_module_raises_an_http_exception() -> None:
    elsewhere = [
        path for path in source_files() if relative(path) not in WHERE_A_STATUS_IS_ANSWERED
    ]
    assert deciding_a_status(elsewhere) == []


def test_the_rule_catches_a_door_deciding_a_status() -> None:
    assert deciding_a_status([FIXTURES / "rule06/door.py"]) == [str(FIXTURES / "rule06/door.py")]
