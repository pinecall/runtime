"""The gateway serves one page: the tenant's console at `/`, and every screen under it."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from pinecall._settings import Settings
from pinecall.api import pages
from pinecall.api.app import app
from pinecall.api.pages import NOT_BUILT

pytestmark = pytest.mark.unit

THE_PAGE = (
    "<!doctype html><html><head><title>c</title></head><body><div id=root></div></body></html>"
)
AN_ASSET = "console.log('the console')"

# Every shape a browser asks for: the root, a screen, a deep screen, an asset, the page by name.
SCREENS = ("/", "/a/clinica-norte", "/a/clinica-norte/sessions/call_1", "/keys", "/index.html")


@pytest.fixture
def built(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A console as scripts/console leaves it: the page, and one asset beside it."""
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text(THE_PAGE)
    (tmp_path / "assets" / "app.js").write_text(AN_ASSET)
    monkeypatch.setattr(pages, "CONSOLE", pages.Page(name="console", directory=tmp_path))
    return tmp_path


@pytest.mark.parametrize("path", SCREENS)
def test_every_screen_is_the_page_so_a_reload_lands_where_it_was(
    gateway: TestClient,
    built: Path,  # noqa: ARG001 — the fixture is the built console, in place
    path: str,
) -> None:
    status, content_type, body = fetched(gateway, path)
    assert status == 200
    assert content_type.startswith("text/html")
    assert body == THE_PAGE


def test_an_asset_the_build_wrote_is_served_as_itself(
    gateway: TestClient,
    built: Path,  # noqa: ARG001
) -> None:
    status, content_type, body = fetched(gateway, "/assets/app.js")
    assert status == 200
    assert "javascript" in content_type
    assert body == AN_ASSET


def test_a_path_under_the_api_that_no_door_declared_is_still_a_json_404(
    gateway: TestClient,
    built: Path,  # noqa: ARG001
) -> None:
    """The page never stands in for a door that is not there."""
    for path in ("/v1/nope", "/v1/agents/x/nothing", "/.well-known/nothing"):
        status, content_type, body = fetched(gateway, path)
        assert status == 404, path
        assert content_type.startswith("application/json"), path
        assert json.loads(body) == {"detail": "Not Found"}, path


def test_a_url_cannot_climb_out_of_the_consoles_directory(
    gateway: TestClient,
    built: Path,  # noqa: ARG001
) -> None:
    status, _, body = fetched(gateway, "/assets/../../pyproject.toml")
    assert status == 200 and body == THE_PAGE, "a climb is a screen nobody has: the page"


def test_a_gateway_nobody_built_the_console_into_says_so(
    gateway: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pages, "CONSOLE", pages.Page("console", tmp_path / "never-built"))
    status, content_type, body = fetched(gateway, "/a/clinica-norte")
    assert status == 404
    assert content_type.startswith("application/json")
    assert json.loads(body) == {"detail": NOT_BUILT.format(page="console")}


def test_exactly_one_catch_all_and_it_is_the_last_route() -> None:
    """Every door names its own path; the one `{path:path}` is the console's, after all of them.

    There is one page now: the operator had a second bundle under `/admin` and it is gone, so a
    path that is not a door and not a file is a screen of the console, whoever is reading it."""
    declared = _every_path(app.routes)
    catch_alls = [path for path in declared if path.endswith("{path:path}")]
    assert catch_alls == ["/{path:path}"]
    assert declared[-1] == "/{path:path}"


# FastAPI keeps an included router as one route holding routes of its own, so the paths are read
# by walking down: a flat read of app.routes sees no path at all and would pass on nothing.
def _every_path(routes: Any) -> list[str]:
    """The path of every route the app answers, in the order it tries them."""
    found: list[str] = []
    for route in routes:
        included = getattr(route, "original_router", None)
        if included is not None:
            found.extend(_every_path(included.routes))
        else:
            found.append(str(getattr(route, "path", "")))
    return found


def test_the_well_known_door_says_which_runtime_and_whose(gateway: TestClient) -> None:
    status, content_type, body = fetched(gateway, "/.well-known/pinecall")
    assert status == 200 and content_type.startswith("application/json")
    said = json.loads(body)
    # The floor rides here because a page asking for a password must say the rule before anybody
    # types, and it holds no key when it asks. The suite's settings take the default.
    assert said == {
        "version": said["version"],
        "cloud": False,
        "signup": False,
        "min_password": Settings().min_password,
        # Whether the BOX can post a letter, so a sign-in page knows whether "Forgot your
        # password?" may promise an email. This suite's settings name no mail server.
        "mail": False,
        # And what the box is called, so the same page draws the operator's name before a key.
        "brand": {"name": "Pinecall", "logo_url": None, "accent": "#5b3df5"},
        # …and whether it may offer "Continue with Google": nobody wired one here.
        "google": False,
    }
    assert isinstance(said["version"], str)


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


def allowed_origin(gateway: TestClient, path: str) -> str | None:
    """The CORS header one GET at the gateway answers with, or None."""
    got: Any = gateway.get(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        path
    )
    said = got.headers.get(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        "access-control-allow-origin"
    )
    return None if said is None else str(said)  # pyright: ignore[reportUnknownArgumentType]


def test_the_widget_is_served_from_the_gateway_with_cors_for_any_site(
    gateway: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gateway is the widget's CDN: one module, fetched cross-origin by a site's page."""
    (tmp_path / "widget").mkdir()
    (tmp_path / "widget" / "pinecall-widget.js").write_text(AN_ASSET)
    monkeypatch.setattr(pages, "WIDGET", tmp_path / "widget")
    status, kind, body = fetched(gateway, "/widget/pinecall-widget.js")
    assert status == 200
    assert "javascript" in kind
    assert allowed_origin(gateway, "/widget/pinecall-widget.js") == "*"
    assert body == AN_ASSET
    # Nothing else under /widget is a page: a wrong name is a 404 and never index.html.
    assert fetched(gateway, "/widget/nope.js")[0] == 404


# `/docs` is a SCREEN of the console — the org's knowledge bases — and FastAPI mounts its own
# Swagger there by default. A route wins over the catch-all that serves the page, so the API's
# schema shadowed the screen: a pasted link opened Swagger and a reload threw a person out of the
# console (production, 2026-09-20). The interactive schema lives under `/v1` with every other door.
def test_the_console_owns_docs_and_the_schema_lives_under_v1(
    gateway: TestClient,
    built: Path,  # noqa: ARG001 — the fixture is the built console, in place
) -> None:
    handle: Any = gateway
    for screen in ("/docs", "/docs/some-base"):
        page: Any = handle.get(screen)
        assert page.status_code == 200
        assert page.text == THE_PAGE
    swagger: Any = handle.get("/v1/docs")
    assert swagger.status_code == 200
    assert "swagger" in swagger.text.lower()
    assert handle.get("/openapi.json").status_code == 200
