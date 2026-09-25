"""CORS on /v1: the mobile app and the origins PINECALL_APP_ORIGINS names, and nobody else."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from pinecall._settings import Settings
from pinecall.api import pages
from pinecall.api.app import app
from pinecall.api.app_origins import THE_APPS_WEBVIEWS, origins_allowed
from pinecall.log.writers import Logs
from tests.api.conftest import A_KEY

pytestmark = pytest.mark.unit

IOS = "capacitor://localhost"
ANDROID = "https://localhost"
A_DEV_SERVER = "http://localhost:5173"
A_STRANGER = "https://evil.example"
WHOAMI = "/v1/whoami"
CALL = "call_the_app_reads"
BEARER = {"Authorization": f"Bearer {A_KEY}"}
# What the app's fetch makes the browser ask before any door that carries the key.
ASKING = {
    "Access-Control-Request-Method": "GET",
    "Access-Control-Request-Headers": "authorization, pinecall-env, pinecall-corner, last-event-id",
}


# starlette's TestClient types its requests through httpx's private `_types`, which no checker can
# resolve, so every request goes through here: the ignores in one place, and the tests below read
# a status and the headers, lower-cased, as a browser would compare them.
def asked(
    gateway: TestClient, method: str, path: str, headers: dict[str, str]
) -> tuple[int, dict[str, str]]:
    """One request from a page somewhere: the status, and every header of the answer."""
    got: Any = gateway.request(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        method, path, headers=headers
    )
    return (
        int(got.status_code),  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
        {str(k).lower(): str(v) for k, v in got.headers.items()},  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType, reportUnknownArgumentType]
    )


def cors_of(headers: dict[str, str]) -> dict[str, str]:
    """Only the CORS headers of an answer."""
    return {name: value for name, value in headers.items() if name.startswith("access-control-")}


# An operator's shell may export the variable; the suite reads the list only where a test sets it.
@pytest.fixture(autouse=True)
def no_origins_exported(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test starts from the unset list."""
    monkeypatch.delenv("PINECALL_APP_ORIGINS", raising=False)


@pytest.fixture
def a_dev_server_named(monkeypatch: pytest.MonkeyPatch, wired: None) -> Iterator[TestClient]:  # noqa: ARG001
    """A gateway started with PINECALL_APP_ORIGINS naming the app's dev server, and a blank."""
    monkeypatch.setenv("PINECALL_APP_ORIGINS", f" {A_DEV_SERVER} , ,")
    with TestClient(app) as client:
        yield client


# ── the allowlist ───────────────────────────────────────────────────────────────


def test_unset_the_list_is_the_apps_two_webviews_and_nothing_more() -> None:
    assert origins_allowed(Settings(world="production")) == THE_APPS_WEBVIEWS == (IOS, ANDROID)


def test_the_variable_adds_an_origin_after_the_two_and_never_twice() -> None:
    settings = Settings(world="production", app_origins=f"{A_DEV_SERVER},{IOS}")
    assert origins_allowed(settings) == (IOS, ANDROID, A_DEV_SERVER)


# ── the app's own origins ───────────────────────────────────────────────────────


@pytest.mark.parametrize("origin", [IOS, ANDROID])
def test_the_app_reads_a_door_and_its_origin_is_echoed(gateway: TestClient, origin: str) -> None:
    status, headers = asked(gateway, "GET", WHOAMI, {**BEARER, "Origin": origin})
    assert status == 200
    assert headers["access-control-allow-origin"] == origin
    assert "origin" in headers["vary"].lower()
    assert "access-control-allow-credentials" not in headers


@pytest.mark.parametrize("origin", [IOS, ANDROID])
def test_the_apps_preflight_is_answered_with_what_it_may_send(
    gateway: TestClient, origin: str
) -> None:
    status, headers = asked(gateway, "OPTIONS", WHOAMI, {"Origin": origin, **ASKING})
    assert status == 200
    assert headers["access-control-allow-origin"] == origin
    assert "origin" in headers["vary"].lower()
    methods = headers["access-control-allow-methods"].split(", ")
    assert set(methods) == {"GET", "POST", "PUT", "PATCH", "DELETE"}
    allowed = headers["access-control-allow-headers"].lower().split(", ")
    for header in ("authorization", "content-type", "pinecall-env", "pinecall-corner"):
        assert header in allowed
    assert {"last-event-id", "accept"} <= set(allowed)
    assert headers["access-control-max-age"] == "600"
    assert "access-control-allow-credentials" not in headers


async def test_the_stream_of_a_call_carries_the_echo_too(gateway: TestClient, logs: Logs) -> None:
    log = logs.writing(CALL, "clara")
    for type in ("call.started", "call.score"):
        await log.append(type, {})
    streaming = {**BEARER, "Accept": "text/event-stream", "Last-Event-ID": "0", "Origin": IOS}
    status, headers = asked(gateway, "GET", f"/v1/calls/{CALL}/events", streaming)
    assert status == 200
    assert headers["content-type"].startswith("text/event-stream")
    assert headers["access-control-allow-origin"] == IOS


# ── everybody else ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("origin", [A_STRANGER, A_DEV_SERVER, "null"])
def test_an_origin_not_on_the_list_gets_no_cors_header_at_all(
    gateway: TestClient, origin: str
) -> None:
    _, read = asked(gateway, "GET", WHOAMI, {**BEARER, "Origin": origin})
    _, preflight = asked(gateway, "OPTIONS", WHOAMI, {"Origin": origin, **ASKING})
    assert cors_of(read) == {}
    assert cors_of(preflight) == {}


def test_a_request_with_no_origin_is_answered_as_before(gateway: TestClient) -> None:
    status, headers = asked(gateway, "GET", WHOAMI, BEARER)
    assert status == 200
    assert cors_of(headers) == {}


def test_the_dev_server_is_let_in_only_when_the_variable_names_it(
    a_dev_server_named: TestClient,
) -> None:
    _, read = asked(a_dev_server_named, "GET", WHOAMI, {**BEARER, "Origin": A_DEV_SERVER})
    _, preflight = asked(a_dev_server_named, "OPTIONS", WHOAMI, {"Origin": A_DEV_SERVER, **ASKING})
    _, stranger = asked(a_dev_server_named, "GET", WHOAMI, {**BEARER, "Origin": A_STRANGER})
    assert read["access-control-allow-origin"] == A_DEV_SERVER
    assert preflight["access-control-allow-origin"] == A_DEV_SERVER
    assert cors_of(stranger) == {}


# ── what is not a door ──────────────────────────────────────────────────────────


def test_the_widget_keeps_its_star_whoever_asks(
    gateway: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "widget").mkdir()
    (tmp_path / "widget" / "pinecall-widget.js").write_text("export {}")
    monkeypatch.setattr(pages, "WIDGET", tmp_path / "widget")
    for origin in (IOS, A_STRANGER):
        _, headers = asked(gateway, "GET", "/widget/pinecall-widget.js", {"Origin": origin})
        assert cors_of(headers) == {"access-control-allow-origin": "*"}


# ── a tenant's page, reading its visitor's call ─────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [f"/v1/calls/{CALL}/events", f"/v1/calls/{CALL}/state", f"/v1/calls/{CALL}/recording"]
    + ["/v1/codes/0427"],
)
def test_any_page_may_read_a_calls_own_doors_and_a_codes_with_a_bearer_and_no_credentials(
    gateway: TestClient, path: str
) -> None:
    asking = {
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "authorization, last-event-id",
    }
    status, preflight = asked(gateway, "OPTIONS", path, {"Origin": A_STRANGER, **asking})
    assert status == 200
    assert preflight["access-control-allow-origin"] == "*"
    assert "access-control-allow-credentials" not in preflight
    _, read = asked(gateway, "GET", path, {**BEARER, "Origin": A_STRANGER})
    assert read["access-control-allow-origin"] == "*"


def test_a_page_may_not_write_to_a_call_or_read_anything_else(gateway: TestClient) -> None:
    for path in (
        f"/v1/calls/{CALL}/verbs",
        f"/v1/calls/{CALL}/events/extra",
        "/v1/codes",
        "/v1/codes/0427/extra",
        WHOAMI,
        "/v1/agents/x/calls",
    ):
        _, answer = asked(gateway, "GET", path, {**BEARER, "Origin": A_STRANGER})
        assert cors_of(answer) == {}, path
    asking = {"Access-Control-Request-Method": "POST"}
    _, preflight = asked(
        gateway, "OPTIONS", f"/v1/calls/{CALL}/events", {"Origin": A_STRANGER, **asking}
    )
    assert (
        "access-control-allow-methods" not in preflight
        or "POST" not in preflight["access-control-allow-methods"]
    )
