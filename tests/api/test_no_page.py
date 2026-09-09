"""The gateway is an API: no path it did not declare answers, and nothing it serves is a page."""

from __future__ import annotations

import json
from typing import Any

import pytest
from starlette.testclient import TestClient

from pinecall.api.app import app

pytestmark = pytest.mark.unit

# The shapes a served console would have answered for: the root a browser opens, a screen under
# it, and an asset beside it. The console left on 2026-09-08 (docs/decisions/console.md), and a
# catch-all that came back would show up here long before it showed up on a box.
NOT_DOORS = ("/", "/a/clinica-norte", "/a/clinica-norte/talk", "/assets/console.js", "/index.html")


@pytest.mark.parametrize("path", NOT_DOORS)
def test_a_path_the_gateway_never_declared_is_a_json_404(gateway: TestClient, path: str) -> None:
    status, content_type, body = fetched(gateway, path)

    assert status == 404
    assert content_type.startswith("application/json")
    assert json.loads(body) == {"detail": "Not Found"}


def test_the_gateway_declares_no_catch_all_route() -> None:
    """Every route names its own path: a `{path:path}` route is a page waiting to happen."""
    declared = [str(getattr(route, "path", "")) for route in app.routes]

    assert [path for path in declared if "{path:path}" in path] == []


# starlette's TestClient types its requests through httpx's private `_types`, which no checker can
# resolve, so the one request this file makes goes through here: the ignores live in one place.
def fetched(gateway: TestClient, path: str) -> tuple[int, str, str]:
    """One GET at the gateway: the status, the content type, and the body as text."""
    got: Any = gateway.get(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        path
    )
    return (
        int(got.status_code),  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
        str(got.headers["content-type"]),  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
        str(got.text),  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
    )
