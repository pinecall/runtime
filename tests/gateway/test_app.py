"""Tests for the gateway process: its doors, the one answer to a refusal, CORS, the pages."""

from collections.abc import Callable

import httpx
from fastapi import Request
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from psycopg.errors import QueryCanceled
from psycopg_pool import PoolTimeout

from pinecall.domain.errors import Conflict, NotSignedIn
from pinecall.gateway._deps import SCOPES_OF, operator
from pinecall.gateway.app import ROUTERS, app, busy, origins_allowed, refused
from pinecall.process.settings import Settings
from tests.conftest import Knocking, postgres

# The doors a key opens without a scope: what the fleet's key and a page's token read are
# checked inside; /metrics is fenced by the address it is asked from.
NO_SCOPE = frozenset({"/{path:path}", "/widget/{file}", "/metrics"})

# The doors nobody holds a key at yet (a sign-in page, a terminal, an invitation, Meta), and the
# ones that read any key as who it is (whoami, a code, the org switch, one's own keys). LiveKit's
# webhook, like Meta's, is signed by the sender instead.
NO_KEY_OR_ANY_KEY = frozenset(
    {
        "/.well-known/pinecall",
        "/v1/invitations/{token}",
        "/v1/keys",
        "/v1/keys/{fingerprint}/revoke",
        "/v1/limits",
        "/v1/livekit/webhook",
        "/v1/login",
        "/v1/login/codes",
        "/v1/login/google",
        "/v1/login/google/callback",
        "/v1/login/org",
        "/v1/login/orgs",
        "/v1/login/pairings",
        "/v1/login/pairings/{code}",
        "/v1/login/pairings/{code}/key",
        "/v1/login/reset",
        "/v1/login/sso",
        "/v1/login/sso/callback",
        "/v1/login/sso/discover",
        "/v1/signup",
        "/v1/signup/resend",
        "/v1/signup/verify",
        "/v1/whatsapp/webhook",
        "/v1/whoami",
    }
)


def scopes_of(dependant: Dependant) -> list[Callable[..., object]]:
    """Every scoped dependency a door declares, however deep."""
    found = [
        dependency.call
        for dependency in dependant.dependencies
        if dependency.call in SCOPES_OF or dependency.call is operator
    ]
    for dependency in dependant.dependencies:
        found += scopes_of(dependency)
    return [item for item in found if item is not None]


# FastAPI keeps an included router whole, so its doors are walked through ROUTERS.
def test_every_door_declares_exactly_one_scope() -> None:
    routes = [*app.routes, *(route for doors in ROUTERS for route in doors.router.routes)]
    doors = [route for route in routes if isinstance(route, APIRoute)]
    assert doors
    unscoped = [
        f"{sorted(door.methods or set[str]())} {door.path}"
        for door in doors
        if door.path not in NO_SCOPE | NO_KEY_OR_ANY_KEY
        and len(set(scopes_of(door.dependant))) != 1
    ]
    assert unscoped == []


def test_the_console_is_the_last_route_and_the_only_catch_all() -> None:
    paths = [getattr(route, "path", "") for route in app.routes]
    assert paths[-1] == "/{path:path}"
    assert paths.count("/{path:path}") == 1


async def test_a_refusal_is_its_status_and_its_sentence() -> None:
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    answered = await refused(request, Conflict("the corner moved"))
    assert answered.status_code == 409
    assert answered.body == b'{"detail":"the corner moved"}'


async def test_a_full_pool_and_a_statement_past_its_timeout_are_a_503_in_one_sentence() -> None:
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    waited = await busy(request, PoolTimeout("couldn't get a connection after 2.00 sec"))
    assert waited.status_code == 503
    assert waited.body == (
        b'{"detail":"the database is busy: no connection came free within 2 s; try again"}'
    )
    cancelled = await busy(request, QueryCanceled("canceling statement due to statement timeout"))
    assert cancelled.status_code == 503
    assert cancelled.body == b'{"detail":"the database took more than 30 s to answer; try again"}'


def test_the_apps_two_webviews_are_always_let_in_and_the_variable_adds_after_them() -> None:
    settings = Settings.model_validate(
        {"PINECALL_APP_ORIGINS": "https://console.example, https://localhost"}
    )
    assert origins_allowed(settings) == (
        "capacitor://localhost",
        "https://localhost",
        "https://console.example",
    )


@postgres
async def test_a_door_without_a_key_asks_for_a_bearer(knocking: Knocking) -> None:
    async with httpx.AsyncClient(base_url=knocking.url) as nobody:
        refused_now = await nobody.get("/v1/agents")
    assert refused_now.status_code == 401
    assert refused_now.headers["www-authenticate"] == "Bearer"
    assert NotSignedIn.status == 401


@postgres
async def test_an_allowed_origin_is_echoed_and_another_gets_nothing(knocking: Knocking) -> None:
    async with knocking.http(knocking.app["sandbox"]) as tenant:
        app_origin = await tenant.get("/v1/agents", headers={"Origin": "capacitor://localhost"})
        stranger = await tenant.get("/v1/agents", headers={"Origin": "https://evil.example"})
    assert app_origin.headers["access-control-allow-origin"] == "capacitor://localhost"
    assert "access-control-allow-origin" not in stranger.headers


@postgres
async def test_any_page_may_read_a_calls_own_doors_with_its_token(knocking: Knocking) -> None:
    async with httpx.AsyncClient(base_url=knocking.url) as page:
        answered = await page.get(
            "/v1/calls/call_1/state", headers={"Origin": "https://shop.example"}
        )
    assert answered.headers["access-control-allow-origin"] == "*"


@postgres
async def test_a_path_under_the_api_nobody_declared_is_a_json_404(knocking: Knocking) -> None:
    async with httpx.AsyncClient(base_url=knocking.url) as anybody:
        answered = await anybody.get("/v1/nothing/here")
    assert answered.status_code == 404
    assert answered.json() == {"detail": "Not Found"}


@postgres
async def test_a_gateway_nobody_built_the_console_into_says_so(knocking: Knocking) -> None:
    async with httpx.AsyncClient(base_url=knocking.url) as browser:
        page = await browser.get("/calls/123")
        widget = await browser.get("/widget/pinecall-widget.js")
    assert page.status_code == 404
    assert "make deploy" in page.json()["detail"]
    assert widget.status_code == 404
