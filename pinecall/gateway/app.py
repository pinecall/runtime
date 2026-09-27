"""The gateway process: wired once at start, its doors, the one answer to a refusal, its pages."""

import asyncio
import logging
import re
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.datastructures import Headers
from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from pinecall.channels.routes import server_of
from pinecall.channels.telephony import rebuild
from pinecall.domain.errors import NotSignedIn, PinecallError, SettingsRefused
from pinecall.domain.settings import Settings, load
from pinecall.fleet.hub import Roster
from pinecall.gateway import deps
from pinecall.gateway.api import agents, calls, telephony, whatsapp
from pinecall.gateway.deps import Wired
from pinecall.gateway.live import Gated, Live, Registry, reap_forever
from pinecall.gateway.threads import Threads
from pinecall.log.log import Logs
from pinecall.log.store import Store
from pinecall.postgres.pool import open_pool
from pinecall.tenancy.agents import Codes
from pinecall.tenancy.keys import Signer
from pinecall.tenancy.vault import vault_of

logger = logging.getLogger(__name__)

NO_LIVEKIT = "LIVEKIT_API_KEY and LIVEKIT_API_SECRET: the gateway signs every room token with them"


# ── the process ──


# Everything opened goes on the stack: a failed start closes what opened, a stop closes it all.
@asynccontextmanager
async def lifespan(gateway: FastAPI) -> AsyncGenerator[None]:
    """Wire the box once, run the reaper, and close all of it after."""
    settings = load()
    async with AsyncExitStack() as stack:
        box = await wire(settings, stack)
        gateway.state.wired = box
        reaper = asyncio.create_task(reap_forever(box.gated, box.server))
        stack.push_async_callback(_cancelled, reaper)
        # After the start, so a slow SFU never keeps the gateway from answering.
        rebuilt = asyncio.create_task(rebuild(box.exchange))
        stack.push_async_callback(_cancelled, rebuilt)
        yield


async def wire(settings: Settings, stack: AsyncExitStack) -> Wired:
    """Open what every door shares: the vault first, since a box without its key starts nothing."""
    sealed = vault_of(settings.vault_key)
    if not settings.livekit_api_key or not settings.livekit_api_secret:
        raise SettingsRefused(NO_LIVEKIT)
    pool = await open_pool(settings.database_url)
    stack.push_async_callback(pool.close)
    server = server_of(settings)
    stack.push_async_callback(server.aclose)
    http = httpx.AsyncClient()
    stack.push_async_callback(http.aclose)
    logs = Logs(Store(pool))
    codes = Codes(logs)
    await codes.loaded()
    registry, live = Registry(logs), Live()
    gated = Gated(pool=pool, vault=sealed, logs=logs, live=live)
    threads = Threads(gated, registry, http, settings.timezone)
    await threads.loaded()
    stack.push_async_callback(threads.closed)
    return Wired(
        settings=settings,
        pool=pool,
        vault=sealed,
        logs=logs,
        registry=registry,
        live=live,
        roster=Roster(),
        codes=codes,
        signer=Signer(settings.livekit_api_key, settings.livekit_api_secret),
        server=server,
        closing=asyncio.Event(),
        http=http,
        threads=threads,
    )


async def _cancelled[T](task: asyncio.Task[T]) -> None:
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


# Set from the signal handler, through the loop: streams never end on their own, and a stop
# would otherwise wait out the grace period and cut them mid-frame.
def announce_closing(gateway: FastAPI) -> None:
    """Tell every open stream the process is stopping."""
    box: object = getattr(gateway.state, "wired", None)
    if isinstance(box, Wired):
        asyncio.get_running_loop().call_soon_threadsafe(box.closing.set)


# ── one answer to every refusal ──


async def refused(_request: Request, error: Exception) -> Response:
    """A refusal answered with its status and its sentence."""
    status = error.status if isinstance(error, PinecallError) else 500
    headers = {"WWW-Authenticate": "Bearer"} if isinstance(error, NotSignedIn) else None
    return JSONResponse({"detail": str(error)}, status_code=status, headers=headers)


# ── who may call /v1 from a browser ──

# Capacitor's WebView origins: iOS serves from capacitor://localhost, Android from https://localhost.
THE_APPS_WEBVIEWS = ("capacitor://localhost", "https://localhost")
DOORS = "/v1/"
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
# A bearer, never a cookie: Allow-Credentials is never sent.
ALLOWED_HEADERS = ("authorization", "content-type", deps.WORLD, deps.LOOKING_AT, "last-event-id")
PREFLIGHT_KEPT_S = 600
# A token of one call reads its own doors from any page: GET only, no credentials.
A_PAGES_READS = re.compile(r"^/v1/(calls/[^/]+/(events|state|recording)|codes/[^/]+)$")
READ_HEADERS = ("authorization", "last-event-id", "accept", "range")
READ_EXPOSED = ("content-range", "accept-ranges", "content-length")


# Read per request: the settings are loaded by the lifespan, after the middleware is built.
class AppOrigins:
    """CORS on /v1 for the allowed origins; any page may read what a call's token reads."""

    def __init__(self, app: ASGIApp) -> None:
        """Nothing allowed until the settings say what."""
        self.app = app
        self.cors: tuple[tuple[str, ...], CORSMiddleware] | None = None
        self.any_page = CORSMiddleware(
            app,
            allow_origins=("*",),
            allow_methods=("GET",),
            allow_headers=READ_HEADERS,
            expose_headers=READ_EXPOSED,
            max_age=PREFLIGHT_KEPT_S,
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Route an allowed origin's /v1 request through CORS, and anything else straight in."""
        if scope["type"] != "http" or not str(scope["path"]).startswith(DOORS):
            await self.app(scope, receive, send)
            return
        origin = Headers(scope=scope).get("origin")
        box: object = getattr(scope["app"].state, "wired", None)
        allowed = origins_allowed(box.settings) if isinstance(box, Wired) and origin else ()
        if origin and origin not in allowed and A_PAGES_READS.match(str(scope["path"])):
            await self.any_page(scope, receive, send)
            return
        if not origin or origin not in allowed:
            await self.app(scope, receive, send)
            return
        await self._cors_for(allowed)(scope, receive, send)

    def _cors_for(self, allowed: tuple[str, ...]) -> CORSMiddleware:
        if self.cors is None or self.cors[0] != allowed:
            cors = CORSMiddleware(
                self.app,
                allow_origins=allowed,
                allow_methods=METHODS,
                allow_headers=ALLOWED_HEADERS,
                max_age=PREFLIGHT_KEPT_S,
            )
            self.cors = (allowed, cors)
        return self.cors[1]


def origins_allowed(settings: Settings) -> tuple[str, ...]:
    """The app's two WebViews, then PINECALL_APP_ORIGINS, each once."""
    named = (one.strip() for one in settings.app_origins.split(","))
    return tuple(dict.fromkeys((*THE_APPS_WEBVIEWS, *(one for one in named if one))))


# ── the pages ──

# The console and the widget the wheel carries (hatch_build.py puts them here).
BUILT = Path(__file__).resolve().parents[1] / "public"
THE_PAGE = "index.html"
API_PREFIXES = ("v1/", ".well-known/")
NOT_BUILT = "the console is not built into this gateway: `make deploy` builds it in"
WIDGET_HEADERS = {"Access-Control-Allow-Origin": "*", "Cache-Control": "public, max-age=300"}


def widget(file: str) -> FileResponse:
    """A file of the widget, for any site to load as a module."""
    root = (BUILT / "widget").resolve()
    asked = (root / file).resolve()
    if root not in asked.parents or not asked.is_file():
        raise HTTPException(404, "Not Found")
    return FileResponse(asked, headers=WIDGET_HEADERS)


# The last route: a built file is itself, any other path is the page, so a reload lands where it
# was. Never cached: a rebuild names new hashed assets in a new index.html.
def console(path: str) -> Response:
    """The console: an asset of its build, or its page."""
    if path.startswith(API_PREFIXES):
        raise HTTPException(404, "Not Found")
    root = (BUILT / "console").resolve()
    if not (root / THE_PAGE).is_file():
        raise HTTPException(404, NOT_BUILT)
    asked = (root / path).resolve()
    if path and root in asked.parents and asked.is_file() and asked.name != THE_PAGE:
        return FileResponse(asked)
    page = (root / THE_PAGE).read_text(encoding="utf-8")
    return HTMLResponse(page, headers={"cache-control": "no-store"})


# Swagger under /v1: the console has a /docs screen of its own.
app = FastAPI(
    title="Pinecall gateway", lifespan=lifespan, docs_url="/v1/docs", redoc_url="/v1/redoc"
)
app.include_router(calls.router)
app.include_router(agents.router)
app.include_router(telephony.router)
app.include_router(whatsapp.router)
app.add_api_route("/widget/{file}", widget, methods=["GET"], include_in_schema=False)
app.add_api_route("/{path:path}", console, methods=["GET"], include_in_schema=False)
app.add_exception_handler(PinecallError, refused)
app.add_middleware(AppOrigins)
