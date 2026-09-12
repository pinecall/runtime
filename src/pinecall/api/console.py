"""The console, served by the gateway at `/`: the built page, its assets, and one SPA fallback."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter()

# Where the built console lives inside the distribution: package data the agents repo's vite
# build wrote and `scripts/console` copied here. Git-ignored, because it is a build and not a
# source; shipped in the wheel as an artifact. A checkout that never ran the script has no page,
# and the door says so rather than answering a blank one.
CONSOLE = Path(__file__).resolve().parents[1] / "gateway" / "console"
THE_PAGE = "index.html"

# The API's own prefixes: a path under them that no router declared is a JSON 404, exactly as it
# was before the console moved in. The page never stands in for a door that is not there.
API_PREFIXES = ("v1/", ".well-known/")

NOT_BUILT = (
    "the console is not built into this gateway: run `scripts/console` in the runtime checkout "
    "(it builds the agents repo's console and copies it in), then start the gateway again"
)


# The one catch-all of the whole gateway, included LAST by api/app.py so every /v1 door is
# matched before it. A file the build wrote is served as itself; anything else is the page,
# because the console's router owns the path — /a/<slug>/talk is a screen, not a file — and a
# reload has to land on exactly the same screen. tests/api/test_the_console_is_served.py.
@router.get("/{path:path}", include_in_schema=False)
async def console(path: str) -> FileResponse:
    """The page for a screen, or one of its assets; a JSON 404 under the API's own prefixes."""
    if path.startswith(API_PREFIXES):
        raise HTTPException(404, "Not Found")
    if not (CONSOLE / THE_PAGE).is_file():
        raise HTTPException(404, NOT_BUILT)
    asked = _under_the_console(path)
    return FileResponse(asked if asked is not None else CONSOLE / THE_PAGE)


def _under_the_console(path: str) -> Path | None:
    """The file this path names inside the console's directory, or None when it names none."""
    if not path or path.endswith("/"):
        return None
    candidate = (CONSOLE / path).resolve()
    # Resolved and compared, so `..` in a URL cannot climb out of the directory the build wrote.
    if CONSOLE.resolve() not in candidate.parents or not candidate.is_file():
        return None
    return candidate
