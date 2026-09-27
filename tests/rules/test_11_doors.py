"""Rule 11: every door of v1 exists in v2; for now, every door PARITY.md says is done."""

import re
from pathlib import Path

import pytest

from tests.rules.tree import FIXTURES, PARITY_MD, V1

EVERY_DOOR = V1 / "docs/protocol/every-door.md"

# A row of the table names one path and its methods, or several paths; every pair is a door.
ROWS_OF_V1 = 133
DOORS_OF_V1 = 193

# The routes of gateway/app.py, read from the app once it exists.
ROUTES: frozenset[tuple[str, str]] = frozenset()

A_CELL = re.compile(r"`([^`]+)`")


def doors_in(table: Path) -> list[tuple[str, str]]:
    """Return every (method, path) the table names, in its order."""
    found: list[tuple[str, str]] = []
    for line in table.read_text(encoding="utf-8").splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 2 or not cells[0].startswith("`"):
            continue
        methods = A_CELL.findall(cells[0])
        found += _doors_of(methods, A_CELL.findall(cells[1]))
    return found


# The second cell is a list of tokens: a full path, a query variant (`?dry_run=true`, skipped),
# a bare method (the same path), `METHOD /path`, or a suffix (`/supervise`) that replaces the
# last segment of the path before it.
def _doors_of(methods: list[str], tokens: list[str]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    path = ""
    pending: list[str] = []
    for token in tokens:
        if token.startswith("?"):
            continue
        if " " in token:
            named, path = token.split(" ", 1)
            found += [(one, _without_query(_resolved(path, found))) for one in [*pending, named]]
            pending = []
            continue
        if not token.startswith("/"):
            pending.append(token)
            continue
        if pending:
            found += [(one, _without_query(_resolved(token, found))) for one in pending]
            pending = []
            continue
        path = _resolved(token, found)
        found += [(one, _without_query(path)) for one in methods]
    found += [(one, found[-1][1]) for one in pending]
    return found


def _resolved(token: str, found: list[tuple[str, str]]) -> str:
    if token.startswith(("/v1", "/.well-known", "/widget", "/{")) or not found:
        return token
    return found[-1][1].rsplit("/", 1)[0] + token


def _without_query(path: str) -> str:
    return path.split("?", 1)[0]


def done_per_parity() -> list[tuple[str, str]]:
    """Return the doors PARITY.md lists under `## Doors done`, as `- METHOD /path` lines."""
    text = PARITY_MD.read_text(encoding="utf-8")
    section = text.split("## Doors done", 1)[1].split("\n## ", 1)[0]
    return [
        (line[2:].split(" ", 1)[0], line[2:].split(" ", 1)[1])
        for line in section.splitlines()
        if line.startswith("- ") and "/" in line
    ]


@pytest.mark.skipif(not EVERY_DOOR.is_file(), reason="the v1 checkout is not beside this one")
def test_the_table_of_v1_names_the_doors_and_this_parser_reads_every_one() -> None:
    rows = [
        line
        for line in EVERY_DOOR.read_text(encoding="utf-8").splitlines()
        if line.startswith("| `")
    ]
    doors = doors_in(EVERY_DOOR)
    assert len(rows) == ROWS_OF_V1
    assert len(doors) == DOORS_OF_V1
    assert len(set(doors)) == DOORS_OF_V1
    assert all(path.startswith("/") and "?" not in path for _, path in doors)


@pytest.mark.skipif(not PARITY_MD.is_file(), reason="PARITY.md is internal: a clean clone has none")
def test_every_door_parity_says_is_done_is_a_route_of_the_gateway() -> None:
    doors = set(doors_in(EVERY_DOOR))
    for door in done_per_parity():
        assert door in doors, f"{door} is not a door of v1"
        assert door in ROUTES, f"{door} is done per PARITY.md and not a route"


def test_the_parser_reads_every_shape_the_table_uses() -> None:
    assert doors_in(FIXTURES / "rule11/every-door.md") == [
        ("WS", "/v1/apps"),
        ("PUT", "/v1/carrier"),
        ("GET", "/v1/carrier"),
        ("DELETE", "/v1/carrier"),
        ("POST", "/v1/numbers"),
        ("GET", "/v1/members"),
        ("POST", "/v1/members"),
        ("PUT", "/v1/ops/orgs/{named}/quotas"),
        ("PUT", "/v1/ops/orgs/{named}/dialling"),
        ("GET", "/v1/provider-keys"),
        ("PUT", "/v1/provider-keys/{vendor}"),
        ("DELETE", "/v1/provider-keys/{vendor}"),
        ("POST", "/v1/calls/{call}/listen"),
        ("POST", "/v1/calls/{call}/supervise"),
        ("GET", "/v1/agents/{slug}/threads"),
        ("GET", "/v1/agents/{slug}/threads/{contact}"),
        ("POST", "/v1/calls"),
        ("POST", "/v1/calls/{call}/events"),
        ("POST", "/v1/calls/{call}/sealed"),
        ("GET", "/v1/calls/{call}/commands"),
        ("GET", "/{path}"),
    ]
