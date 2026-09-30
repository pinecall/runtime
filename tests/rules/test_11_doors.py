"""Rule 11: every door of v1 exists in v2; for now, every door PARITY.md says is done."""

import re
from pathlib import Path

import pytest
from fastapi.routing import APIRoute, APIWebSocketRoute

from pinecall.gateway.api import (
    accounts,
    agents,
    apps,
    box,
    callbacks,
    calls,
    chat,
    desk,
    evals,
    fleet,
    hosting,
    judges,
    keys,
    line,
    members,
    numbers,
    ops,
    org,
    personas,
    pipeline,
    providers,
    relay,
    retrieval,
    runner,
    settings,
    signup,
    sso_login,
    threads,
    usage,
    visitors,
    whatsapp,
    widget,
)
from pinecall.gateway.app import app
from tests.rules.tree import FIXTURES, PARITY_MD, V1

EVERY_DOOR = V1 / "docs/protocol/every-door.md"

# Doors of v1 that are not written again: rule 11 skips them when it reads the table whole.
GONE: tuple[tuple[str, str], ...] = (
    # The sandbox asked production who a person was; one gateway serves both worlds now.
    ("POST", "/v1/login/redeem"),
    # The lexicon is one agent's: its doors are under /v1/agents/{slug}/lexicon.
    ("GET", "/v1/lexicon"),
    ("PUT", "/v1/lexicon"),
    ("GET", "/v1/lexicon/history"),
    # A persona is the agent's: its doors are under /v1/agents/{slug}/personas.
    ("GET", "/v1/personas"),
    ("PUT", "/v1/personas/{name}"),
    ("DELETE", "/v1/personas/{name}"),
    ("GET", "/v1/personas/{name}/runs"),
)

# Two doors of v1 the gateway spells otherwise: one route per dev family, and the widget's files
# under one path.
SPELLED: dict[tuple[str, str], tuple[tuple[str, str], ...]] = {
    ("POST", "/v1/agents/{slug}/dev/{family}/{verb}"): tuple(
        ("POST", f"/v1/agents/{{slug}}/dev/{family}/{{verb}}")
        for family in ("chat", "knowledge", "memory", "view", "evals")
    ),
    ("GET", "/widget/pinecall-widget.js"): (("GET", "/widget/{file}"),),
}

# A row of the table names one path and its methods, or several paths; every pair is a door.
ROWS_OF_V1 = 133
DOORS_OF_V1 = 193


def routes_of_the_gateway() -> frozenset[tuple[str, str]]:
    """Return every (method, path) the gateway answers, spelled as the table of v1 spells them."""
    found: set[tuple[str, str]] = set()
    routers = (
        accounts,
        agents,
        apps,
        box,
        callbacks,
        calls,
        chat,
        desk,
        evals,
        fleet,
        hosting,
        judges,
        keys,
        line,
        members,
        numbers,
        ops,
        org,
        personas,
        pipeline,
        providers,
        relay,
        retrieval,
        runner,
        settings,
        signup,
        sso_login,
        threads,
        usage,
        visitors,
        whatsapp,
        widget,
    )
    for route in [*(route for door in routers for route in door.router.routes), *app.routes]:
        if isinstance(route, APIRoute):
            found |= {(method, _spelled(route.path)) for method in route.methods or ()}
        elif isinstance(route, APIWebSocketRoute):
            found.add(("WS", _spelled(route.path)))
    return frozenset(found)


def _spelled(path: str) -> str:
    return path.replace("{path:path}", "{path}")


ROUTES = routes_of_the_gateway()

A_CELL = re.compile(r"`([^`]+)`")


def doors_in(table: Path) -> list[tuple[str, str]]:
    """Return every (method, path) the table names, in its order."""
    found: list[tuple[str, str]] = []
    for row in table.read_text(encoding="utf-8").splitlines():
        cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
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
            found += [(item, _without_query(_resolved(path, found))) for item in [*pending, named]]
            pending = []
            continue
        if not token.startswith("/"):
            pending.append(token)
            continue
        if pending:
            found += [(item, _without_query(_resolved(token, found))) for item in pending]
            pending = []
            continue
        path = _resolved(token, found)
        found += [(method, _without_query(path)) for method in methods]
    found += [(item, found[-1][1]) for item in pending]
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
        (row[2:].split(" ", 1)[0], row[2:].split(" ", 1)[1])
        for row in section.splitlines()
        if row.startswith("- ") and "/" in row
    ]


@pytest.mark.skipif(not EVERY_DOOR.is_file(), reason="the v1 checkout is not beside this one")
def test_the_table_of_v1_names_the_doors_and_this_parser_reads_every_one() -> None:
    rows = [
        line for row in EVERY_DOOR.read_text(encoding="utf-8").splitlines() if row.startswith("| `")
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


@pytest.mark.skipif(not EVERY_DOOR.is_file(), reason="the v1 checkout is not beside this one")
def test_every_door_of_v1_answers_here_or_is_named_gone() -> None:
    missing = [
        door
        for door in doors_in(EVERY_DOOR)
        if door not in GONE and not all(spelt in ROUTES for spelt in SPELLED.get(door, (door,)))
    ]
    assert missing == []


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


@pytest.mark.skipif(not EVERY_DOOR.is_file(), reason="the v1 checkout is not beside this one")
def test_a_door_of_v1_that_is_gone_is_in_the_table_and_no_route_of_the_gateway() -> None:
    doors = set(doors_in(EVERY_DOOR))
    for door in GONE:
        assert door in doors, f"{door} is not a door of v1"
        assert door not in ROUTES, f"{door} is gone and still a route"
