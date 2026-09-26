"""docs/protocol/every-door.md lists every door the app serves, and no door the app does not."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.routing import APIRoute, APIWebSocketRoute

from pinecall.api.app import app

pytestmark = pytest.mark.unit

THE_PAGE = Path(__file__).parents[2] / "docs" / "protocol" / "every-door.md"

# A path the page writes whole. Anything else in a row — `/sealed` after `/v1/calls/{call}/events`,
# `…/history` after `/v1/agents/{slug}/settings` — continues the previous path's parent.
ABSOLUTE = re.compile(r"^(/v1|/\.well-known|/widget|/\{)")


def test_the_walk_reaches_the_doors() -> None:
    """FastAPI 0.141 wraps an included router; a walk that misses it pins an empty list."""
    assert len(_routed()) > 150


def test_every_door_served_is_on_the_page_and_every_door_on_the_page_is_served() -> None:
    routed, listed = _routed(), _listed(THE_PAGE.read_text())
    unlisted = sorted(f"{m} {p}" for m, p in routed if not any(_same(p, q) and m == n for n, q in listed))
    unrouted = sorted(f"{m} {p}" for m, p in listed if not any(_same(p, q) and m == n for n, q in routed))
    assert not unlisted, f"served but not on the page: {unlisted}"
    assert not unrouted, f"on the page but not served: {unrouted}"


def _routed() -> set[tuple[str, str]]:
    """Every (method, path) the app serves, through whatever wrapper the version puts there."""
    found: set[tuple[str, str]] = set()

    def walk(routes: list[object]) -> None:
        for route in routes:
            inner = getattr(route, "original_router", None)
            if inner is not None:
                walk(inner.routes)
            elif isinstance(route, APIRoute):
                found.update((method, route.path) for method in route.methods)
            elif isinstance(route, APIWebSocketRoute):
                found.add(("WS", route.path))

    walk(list(app.routes))
    return found


def _listed(page: str) -> set[tuple[str, str]]:
    """Every (method, path) a row of the table names."""
    found: set[tuple[str, str]] = set()
    for line in page.splitlines():
        if not line.startswith("| `"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        row_methods = re.findall(r"`([A-Z]+)`", cells[0])
        found.update(_paths_of(cells[1], row_methods))
    return found


def _paths_of(cell: str, row_methods: list[str]) -> list[tuple[str, str]]:
    """The (method, path) pairs one path cell names: a method written before a path is that
    path's; a bare method after the last path is one more method on it."""
    pairs: list[tuple[str, str]] = []
    pending: list[str] = []
    previous: str | None = None
    for token in re.findall(r"`([^`]+)`", cell):
        said = re.match(r"^([A-Z]+)?\s*(\S+)?$", token)
        assert said is not None, token
        method, path = said.group(1), said.group(2)
        if path is None or path.startswith("?") or path.startswith("["):
            if method:
                pending.append(method)
            continue
        path = path.split("?")[0].split("[")[0]
        if not ABSOLUTE.match(path):
            assert previous is not None, token
            path = previous.rsplit("/", 1)[0] + "/" + path.lstrip("…/")
        methods = pending + ([method] if method else [])
        pairs.extend((m, path) for m in methods or row_methods)
        pending, previous = [], path
    if pending and previous is not None:
        pairs.extend((m, previous) for m in pending)
    return pairs


def _same(one: str, other: str) -> bool:
    """Two paths are one door when every segment is equal or either side's is a parameter."""
    ours, theirs = one.strip("/").split("/"), other.strip("/").split("/")
    return len(ours) == len(theirs) and all(
        a == b or a.startswith("{") or b.startswith("{") for a, b in zip(ours, theirs, strict=True)
    )
