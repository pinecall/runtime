"""Rule 11: the page of doors and the gateway's routes agree, every door named and nothing more."""

import re
from pathlib import Path

from fastapi.routing import APIRoute, APIWebSocketRoute
from starlette.routing import Route

from pinecall.gateway.api import (
    accounts,
    agents,
    apps,
    box,
    calibration,
    callbacks,
    calls,
    chat,
    dataset,
    desk,
    entries,
    evals,
    fleet,
    hosting,
    judges,
    keys,
    line,
    members,
    metrics,
    monitors,
    numbers,
    ops,
    org,
    personas,
    pipeline,
    prompts,
    providers,
    recordings,
    relay,
    retrieval,
    runner,
    sessions,
    settings,
    signup,
    simulations,
    sso_login,
    telemetry,
    threads,
    usage,
    visitors,
    webhooks,
    whatsapp,
    widget,
)
from pinecall.gateway.app import app
from tests.rules.tree import FIXTURES, ROOT

# The contract: the page the docs site publishes, which a tenant and an SDK read.
EVERY_DOOR = ROOT / "docs/protocol/every-door.md"


def routes_of_the_gateway() -> frozenset[tuple[str, str]]:
    """Return every (method, path) the gateway answers, a path parameter spelled by its name."""
    found: set[tuple[str, str]] = set()
    routers = (
        accounts,
        agents,
        apps,
        box,
        calibration,
        callbacks,
        calls,
        chat,
        dataset,
        desk,
        entries,
        evals,
        fleet,
        hosting,
        judges,
        keys,
        line,
        members,
        metrics,
        monitors,
        numbers,
        ops,
        org,
        personas,
        pipeline,
        prompts,
        providers,
        recordings,
        relay,
        retrieval,
        runner,
        sessions,
        settings,
        signup,
        simulations,
        sso_login,
        telemetry,
        threads,
        usage,
        visitors,
        webhooks,
        whatsapp,
        widget,
    )
    for route in [*(route for door in routers for route in door.router.routes), *app.routes]:
        if isinstance(route, APIRoute):
            found |= {(method, _spelled(route.path)) for method in route.methods or ()}
        elif isinstance(route, APIWebSocketRoute):
            found.add(("WS", _spelled(route.path)))
        elif isinstance(route, Route):
            found |= {(method, route.path) for method in route.methods or ()}
    return frozenset(found)


def _spelled(path: str) -> str:
    return path.replace("{path:path}", "{path}")


ROUTES = routes_of_the_gateway()

# What the gateway answers that is not a door of the contract: the console's pages, at every path
# no door took, and FastAPI's own schema with the two pages that read it.
NOT_DOORS = frozenset(
    {
        ("GET", "/{path}"),
        *(
            (method, path)
            for method in ("GET", "HEAD")
            for path in ("/openapi.json", "/v1/docs", "/docs/oauth2-redirect", "/v1/redoc")
        ),
    }
)

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


def test_the_page_names_each_door_once_and_the_parser_reads_every_row() -> None:
    rows = [
        line
        for line in EVERY_DOOR.read_text(encoding="utf-8").splitlines()
        if line.startswith("| `")
    ]
    doors = doors_in(EVERY_DOOR)
    assert len(doors) == len(set(doors)) == len(rows)
    assert all(path.startswith("/") and "?" not in path for _, path in doors)


def test_every_door_the_page_names_is_a_route_of_the_gateway() -> None:
    missing = [
        (method, path)
        for method, path in doors_in(EVERY_DOOR)
        if (method, _spelled(path)) not in ROUTES
    ]
    assert missing == []


def unnamed(routes: frozenset[tuple[str, str]], page: Path) -> list[tuple[str, str]]:
    """Return every route the page names nowhere, but what is not a door, in path order."""
    named = {(method, _spelled(path)) for method, path in doors_in(page)}
    return sorted(
        (route for route in routes - NOT_DOORS if route not in named),
        key=lambda route: (route[1], route[0]),
    )


def test_every_route_of_the_gateway_is_a_door_the_page_names() -> None:
    assert unnamed(ROUTES, EVERY_DOOR) == []


def test_the_rule_catches_a_route_the_page_leaves_out_and_not_one_that_is_no_door() -> None:
    routes = frozenset({("GET", "/v1/carrier"), ("GET", "/v1/unnamed"), ("HEAD", "/openapi.json")})
    assert unnamed(routes, FIXTURES / "rule11/every-door.md") == [("GET", "/v1/unnamed")]


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
