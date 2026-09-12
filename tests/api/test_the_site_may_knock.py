"""The landing page knocks from another origin: the sign-up answers it, and only a named site."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from pinecall._settings import Settings, origins_of
from pinecall.api.app import welcomed

pytestmark = pytest.mark.unit

THE_SITE = "https://pinecall.io"
A_STRANGER = "https://not-ours.example"


def a_gateway(site: str) -> TestClient:
    """One door of the shape the sign-up has, with whatever CORS the settings ask for."""
    gateway = FastAPI()

    @gateway.post("/v1/signup")
    async def signup() -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        return {"key": "pk_"}

    welcomed(gateway, Settings(site=site))
    return TestClient(gateway)


# starlette's TestClient is untyped under strict, so every knock goes through one Any handle —
# the same way tests/api/tokens/test_the_door.py holds it.
def preflight(client: TestClient, origin: str) -> tuple[int, str | None]:
    """What the browser sends before the post, and what it reads out of the answer."""
    handle: Any = client
    answer: Any = handle.options(
        "/v1/signup",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    status: int = answer.status_code
    welcomed_origin: str | None = answer.headers.get("access-control-allow-origin")
    return status, welcomed_origin


def posted(client: TestClient, origin: str) -> dict[str, str]:
    """The real post the preflight was about, and the headers the browser reads off it."""
    handle: Any = client
    answer: Any = handle.post("/v1/signup", headers={"Origin": origin})
    headers: dict[str, str] = dict(answer.headers)
    return headers


def test_the_site_named_may_knock_and_nobody_else_may() -> None:
    """The one origin the box names is welcomed; another is not, so the browser refuses the post."""
    client = a_gateway(THE_SITE)
    assert preflight(client, THE_SITE) == (200, THE_SITE)
    allowed, said = preflight(client, A_STRANGER)
    assert said is None, "a stranger's origin is never echoed back"
    assert allowed == 400
    assert posted(client, THE_SITE)["access-control-allow-origin"] == THE_SITE


def test_a_box_with_no_site_answers_no_preflight_at_all() -> None:
    """A gateway with no page in front of it adds nothing: the door says which methods it has."""
    status, said = preflight(a_gateway(""), THE_SITE)
    assert (status, said) == (405, None)


def test_the_sites_are_read_one_per_comma_and_blank_is_none() -> None:
    """A landing page has an apex and a www, and a deploy may add a preview: one line, commas."""
    assert origins_of(Settings(site="https://pinecall.io, https://www.pinecall.io")) == (
        "https://pinecall.io",
        "https://www.pinecall.io",
    )
    assert origins_of(Settings(site="  ")) == ()


def test_no_cookie_ever_rides_a_cross_origin_knock() -> None:
    """The key is a Bearer the page holds, never ambient credentials a site could borrow."""
    assert "access-control-allow-credentials" not in posted(a_gateway(THE_SITE), THE_SITE)
