"""The pages the gateway serves — the console, the operator's page — and the widget at `/widget`."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter()

# Where the built pages live inside the distribution: package data the agents repo's vite builds
# wrote and `scripts/console` copied here. Git-ignored, because a build is not a source; shipped
# in the wheel as artifacts. A checkout that never ran the script has no page, and the door says
# so rather than answering a blank one.
BUILT = Path(__file__).resolve().parents[1] / "gateway"
THE_PAGE = "index.html"

# The API's own prefixes: a path under them that no router declared is a JSON 404, exactly as it
# was before the console moved in. A page never stands in for a door that is not there.
API_PREFIXES = ("v1/", ".well-known/")

NOT_BUILT = (
    "the {page} page is not built into this gateway: run `scripts/console` in the runtime "
    "checkout (it builds the agents repo's pages and copies them in), then start the gateway again"
)


# Two pages, and they share nothing but this shape. The console is the TENANT's, mounted at the
# root so a deep link is a screen; the admin is the OPERATOR's, mounted under `/admin` so it is a
# second program with a second bundle and a second credential — the ops key belongs to no org and
# must never reach a tab that holds a tenant's. docs/decisions/api.md.
@dataclass(frozen=True)
class Page:
    """One built single-page app: the name it says when it is missing, and where its files are."""

    name: str
    directory: Path


CONSOLE = Page(name="console", directory=BUILT / "console")
ADMIN = Page(name="admin", directory=BUILT / "admin")
# Not a page: one script, `<pinecall-widget>`, for any site to load from this gateway as from a
# CDN. A module script from another origin is fetched with CORS, so the header is on every answer.
WIDGET = BUILT / "widget"
WIDGET_HEADERS = {"Access-Control-Allow-Origin": "*", "Cache-Control": "public, max-age=300"}
NO_WIDGET = "no widget is built into this gateway: run `scripts/console` in the runtime checkout"


@router.get("/widget/{file}", include_in_schema=False)
async def widget(file: str) -> FileResponse:
    """The widget's one file, or a 404: nothing under /widget falls back to a page."""
    asked = (WIDGET / file).resolve()
    if WIDGET.resolve() not in asked.parents or not asked.is_file():
        raise HTTPException(404, NO_WIDGET if not WIDGET.is_dir() else "Not Found")
    return FileResponse(asked, headers=WIDGET_HEADERS)


# The operator's page, declared BEFORE the catch-all so `/admin` and everything under it is this
# bundle and never the console's. Its own router owns the paths below it, so a reload on
# /admin/orgs/clinica lands on the same screen.
@router.get("/admin", include_in_schema=False)
@router.get("/admin/{path:path}", include_in_schema=False)
async def admin(path: str = "") -> FileResponse:
    """The operator's page for a screen, or one of its assets."""
    return _served(ADMIN, path)


# The one catch-all of the whole gateway, included LAST by api/app.py so every /v1 door and the
# page above are matched before it. A file the build wrote is served as itself; anything else is
# the page, because the console's router owns the path — /a/<slug>/talk is a screen, not a file —
# and a reload has to land on exactly the same screen. tests/api/test_the_console_is_served.py.
@router.get("/{path:path}", include_in_schema=False)
async def console(path: str) -> FileResponse:
    """The page for a screen, or one of its assets; a JSON 404 under the API's own prefixes."""
    if path.startswith(API_PREFIXES):
        raise HTTPException(404, "Not Found")
    return _served(CONSOLE, path)


def _served(page: Page, path: str) -> FileResponse:
    """One of the page's files, or the page itself — which is what every screen's URL is."""
    if not (page.directory / THE_PAGE).is_file():
        raise HTTPException(404, NOT_BUILT.format(page=page.name))
    asked = _under(page, path)
    return FileResponse(asked if asked is not None else page.directory / THE_PAGE)


def _under(page: Page, path: str) -> Path | None:
    """The file this path names inside the page's directory, or None when it names none."""
    if not path or path.endswith("/"):
        return None
    candidate = (page.directory / path).resolve()
    # Resolved and compared, so `..` in a URL cannot climb out of the directory the build wrote.
    if page.directory.resolve() not in candidate.parents or not candidate.is_file():
        return None
    return candidate
