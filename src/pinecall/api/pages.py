"""The two things the gateway serves beside its doors: the console at `/`, and the widget."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse

from pinecall._settings import Settings
from pinecall.api._deps import SettingsDep
from pinecall.auth.world import the_host
from pinecall.types import PRODUCTION, SANDBOX

router = APIRouter()

# Where the built pages live inside the distribution: package data the console repo's vite build
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


# One page, mounted at the root so a deep link is a screen. The operator had a second program
# under `/admin`, with a second bundle and the box's ops key typed into it; it is gone. The box is
# operated from this same console — the Box screens, which `/v1/ops/*` opens for a person the box
# made an operator — and the first org of a box is made before any page exists, by
# `pinecall-runtime init` on the box itself. docs/decisions/api.md.
@dataclass(frozen=True)
class Page:
    """One built single-page app: the name it says when it is missing, and where its files are."""

    name: str
    directory: Path


CONSOLE = Page(name="console", directory=BUILT / "console")
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


# The one catch-all of the whole gateway, included LAST by api/app.py so every /v1 door and the
# page above are matched before it. A file the build wrote is served as itself; anything else is
# the page, because the console's router owns the path — /a/<slug>/talk is a screen, not a file —
# and a reload has to land on exactly the same screen. tests/api/test_the_pages_are_served.py.
@router.get("/{path:path}", include_in_schema=False)
async def console(path: str, request: Request, settings: SettingsDep) -> Response:
    """The page for a screen, or one of its assets; a JSON 404 under the API's own prefixes."""
    if path.startswith(API_PREFIXES):
        raise HTTPException(404, "Not Found")
    return _served(CONSOLE, path, marks(settings, the_host(request.headers)))


# One bundle, two consoles, and this is the whole of what tells them apart: a box that answers to a
# second name (`PINECALL_SANDBOX_DOMAIN`) serves the sandbox's console there and production's at its
# own, and the page reads which it is out of the page itself (lib/mode.ts). A box with ONE name
# marks nothing, and a page nobody marked is production's — which is what every box was before
# there were two. The second mark is where the OTHER console is, so the switcher's link is the
# box's own answer and not a name compiled into the bundle.
WORLD_MARK = '<meta name="pinecall-world" content="{world}">'
ELSEWHERE_MARK = '<meta name="pinecall-elsewhere" content="{url}">'


def marks(settings: Settings, host: str) -> str:
    """What is written into the head for the name it was asked at; nothing on a box of one name."""
    sandbox = settings.sandbox_domain
    if not sandbox:
        return ""
    here_is_the_sandbox = host == sandbox.lower()
    other = settings.domain if here_is_the_sandbox else sandbox
    said = WORLD_MARK.format(world=SANDBOX if here_is_the_sandbox else PRODUCTION)
    if other:
        said += ELSEWHERE_MARK.format(url=escape(f"https://{other}", quote=True))
    return said


def _served(page: Page, path: str, marked: str) -> Response:
    """One of the page's files, or the page itself — which is what every screen's URL is."""
    if not (page.directory / THE_PAGE).is_file():
        raise HTTPException(404, NOT_BUILT.format(page=page.name))
    asked = _under(page, path)
    return FileResponse(asked) if asked is not None else _the_page(page, marked)


# Read on every request rather than once at startup: it is one small file, and a console rebuilt
# under a running gateway names new assets in a new page. The page is never cached for the same
# reason — the assets it names carry their hash in their names and are cached forever.
def _the_page(page: Page, marked: str) -> HTMLResponse:
    """The console itself, marked with what it has to know before it boots."""
    written = (page.directory / THE_PAGE).read_text(encoding="utf-8")
    return HTMLResponse(
        written.replace("<head>", f"<head>{marked}", 1),
        headers={"cache-control": "no-store"},
    )


def _under(page: Page, path: str) -> Path | None:
    """The file this path names inside the page's directory, or None when it names none."""
    if not path or path.endswith("/"):
        return None
    candidate = (page.directory / path).resolve()
    # Resolved and compared, so `..` in a URL cannot climb out of the directory the build wrote.
    if page.directory.resolve() not in candidate.parents or not candidate.is_file():
        return None
    return candidate
