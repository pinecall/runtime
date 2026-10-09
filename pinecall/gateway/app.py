"""The gateway process: wired once at start, its doors, the one answer to a refusal, its pages."""

import asyncio
import logging
import re
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from psycopg.errors import QueryCanceled
from psycopg_pool import PoolTimeout
from starlette.datastructures import Headers
from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from pinecall.channels.telephony.sip import rebuilt_until_whole
from pinecall.domain.errors import (
    DeclarationRefused,
    NotAvailable,
    NotSignedIn,
    PinecallError,
    SettingsRefused,
    StoreUnreachable,
    Throttled,
)
from pinecall.evals.runs import Runner
from pinecall.fleet.roster import Roster
from pinecall.gateway import _deps
from pinecall.gateway._gateway import Gateway
from pinecall.gateway._served import ServedCalls, Serving
from pinecall.gateway._sockets import Sockets
from pinecall.gateway.api import (
    accounts,
    agents,
    apps,
    box,
    calibration,
    callbacks,
    calls,
    chat,
    dataset,
    desk,
    entries,
    evals,
    fleet,
    hosting,
    judges,
    keys,
    line,
    members,
    metrics,
    monitors,
    numbers,
    ops,
    org,
    personas,
    pipeline,
    prompts,
    providers,
    recordings,
    relay,
    retrieval,
    runner,
    sessions,
    settings,
    signup,
    sso_login,
    telemetry,
    threads,
    usage,
    visitors,
    whatsapp,
    widget,
)
from pinecall.gateway.api.providers import SAMPLES_A_MINUTE
from pinecall.gateway.calls.threads import Threads
from pinecall.gateway.dispatching.sweep import sweep_forever
from pinecall.gateway.ending.reaper import reap_forever
from pinecall.log.logs import Logs
from pinecall.log.store import Store
from pinecall.postgres.pool import TIMEOUTS, box_task
from pinecall.process.connections import Connections, opened
from pinecall.process.settings import Settings, load
from pinecall.providers import catalog
from pinecall.retrieval.embed import Embedder
from pinecall.tenancy.codes import Codes
from pinecall.tenancy.knocks import Throttle
from pinecall.tenancy.mail import Mailbox, Outbox, parse_mailbox_url
from pinecall.tenancy.remembered import RememberedKeys
from pinecall.tenancy.signin import SignIns
from pinecall.tenancy.throttle import Window
from pinecall.tenancy.tokens import Signer
from pinecall.tenancy.vault import box_credentials
from pinecall.tenancy.words import Words

logger = logging.getLogger(__name__)


NO_EMBEDDER = "no providers row: the gateway starts with no embedder"


NO_EMBEDDER_KEY = "the providers row embeds with %s and the box holds no key for it: no embedder"


NO_LIVEKIT = "LIVEKIT_API_KEY and LIVEKIT_API_SECRET: the gateway signs every room token with them"


NO_SIGNING = "PINECALL_TOKEN_KEY: the gateway signs its log and code tokens with it"


NO_BOX_MAIL = "PINECALL_SMTP_URL does not read (%s): the box posts no letter of its own"


# The two ways a database too busy to answer now shows; a worker outlasts a 503 and retries.
NO_CONNECTION = "the database is busy: no connection came free within {wait:g} s; try again"


TOO_SLOW = "the database took more than {took:g} s to answer; try again"


# Capacitor's WebView origins: iOS serves from capacitor://localhost, Android from https://localhost.
THE_APPS_WEBVIEWS = ("capacitor://localhost", "https://localhost")


DOORS = "/v1/"


METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")


# A bearer, never a cookie: Allow-Credentials is never sent.
ALLOWED_HEADERS = ("authorization", "content-type", _deps.WORLD, _deps.LOOKING_AT, "last-event-id")


PREFLIGHT_KEPT_S = 600


# The most any request carries, above every door's own cap (a hold melody 20 MiB, a hosted app's
# source 10 MiB): a body past it is refused as it streams in, never held whole in memory, and the
# webhooks that read a body to check its signature read at most this much of an unsigned one.
LARGEST_BODY = 32 * 1024 * 1024


TOO_LARGE = "the body is over {most} MiB, more than any door takes"


# A token of one call reads its own doors from any page: GET only, no credentials.
A_PAGES_READS = re.compile(r"^/v1/(calls/[^/]+/(events|state|recording)|codes/[^/]+)$")


READ_HEADERS = ("authorization", "last-event-id", "accept", "range")


READ_EXPOSED = ("content-range", "accept-ranges", "content-length")


# The console and the widget the wheel carries (scripts/hatch_build.py puts them here).
BUILT = Path(__file__).resolve().parents[1] / "public"


THE_PAGE = "index.html"


API_PREFIXES = ("v1/", ".well-known/")


NOT_BUILT = "the console is not built into this gateway: `make deploy` builds it in"


WIDGET_HEADERS = {"Access-Control-Allow-Origin": "*", "Cache-Control": "public, max-age=300"}


# The console is framed by nobody (a page that framed it could dress a click as another), reached
# over HTTPS alone once a browser has seen it, read as the type it is served as, and tells another
# site no more of where it came from than its origin (a sign-in lands with a code in the query).
PAGE_HEADERS = {
    "Content-Security-Policy": "frame-ancestors 'none'",
    "X-Frame-Options": "DENY",
    "Strict-Transport-Security": "max-age=31536000",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}


ROUTERS = (
    accounts,
    agents,
    apps,
    box,
    calibration,
    callbacks,
    calls,
    chat,
    dataset,
    desk,
    entries,
    evals,
    fleet,
    hosting,
    judges,
    keys,
    line,
    members,
    metrics,
    monitors,
    numbers,
    ops,
    org,
    personas,
    pipeline,
    prompts,
    providers,
    recordings,
    relay,
    retrieval,
    runner,
    sessions,
    settings,
    signup,
    sso_login,
    telemetry,
    threads,
    usage,
    visitors,
    whatsapp,
    widget,
)


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
        gateway: object = getattr(scope["app"].state, "gateway", None)
        allowed = (
            origins_allowed(gateway.connections.settings)
            if isinstance(gateway, Gateway) and origin
            else ()
        )
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


# Said by Content-Length, refused at once; said by nothing (chunked), counted as it comes and
# answered 413 the chunk it passes the most. The door is then told the client left, so it stops
# reading, and whatever it answers after is dropped: the 413 is the one answer.
class BodyCeiling:
    """No request's body is over LARGEST_BODY."""

    def __init__(self, app: ASGIApp) -> None:
        """The app it stands in front of."""
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Pass the request on, its body counted."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        too_large = JSONResponse(
            {"detail": TOO_LARGE.format(most=LARGEST_BODY // (1024 * 1024))}, status_code=413
        )
        declared = Headers(scope=scope).get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > LARGEST_BODY:
            await too_large(scope, receive, send)
            return
        counted = 0
        answered = False

        async def counting() -> Message:
            nonlocal counted, answered
            message = await receive()
            counted += len(message.get("body", b""))
            if counted > LARGEST_BODY and not answered:
                answered = True
                await too_large(scope, receive, send)
            return {"type": "http.disconnect"} if answered else message

        async def unless_answered(message: Message) -> None:
            if not answered:
                await send(message)

        await self.app(scope, counting, unless_answered)


# Everything opened goes on the stack: a failed start closes what opened, a stop closes it all.
@asynccontextmanager
async def lifespan(fastapi_app: FastAPI) -> AsyncGenerator[None]:
    """Wire the box once, run the reaper, and close all of it after."""
    settings = load()
    async with AsyncExitStack() as stack:
        gateway = await wire(settings, stack)
        fastapi_app.state.gateway = gateway
        reaper = box_task(reap_forever(gateway.serving, gateway.connections.servers))
        stack.push_async_callback(_cancelled, reaper)
        sweep = box_task(sweep_forever(gateway.offering))
        stack.push_async_callback(_cancelled, sweep)
        gateway.loops.update(reaper=reaper, sweep=sweep)
        # After the start, so a slow SFU never keeps the gateway from answering; again until whole.
        rebuilt = box_task(rebuilt_until_whole(gateway.connections))
        stack.push_async_callback(_cancelled, rebuilt)
        yield


async def wire(settings: Settings, stack: AsyncExitStack) -> Gateway:
    """Open the connections, then what the gateway keeps in memory; the reverse at the end."""
    connections = await stack.enter_async_context(opened(settings))
    if not settings.livekit_api_key or not settings.livekit_api_secret:
        raise SettingsRefused(NO_LIVEKIT)
    if not settings.token_key:
        raise SettingsRefused(NO_SIGNING)
    logs = Logs(Store(connections.pool, writing=connections.writing), connections.signal)
    # Pushed before everything that appends on its way out, so it runs after them and before the
    # pool closes: every append a request was promised is written.
    stack.push_async_callback(logs.store.writer.drained)
    stack.push_async_callback(logs.close)
    codes = Codes(logs)
    sockets, live = Sockets(logs), ServedCalls(connections.signal)
    await sockets.start()
    stack.push_async_callback(sockets.close)
    await live.listen()
    stack.push_async_callback(live.quiet)
    embedder = await embedder_of(connections)
    serving = Serving(connections=connections, logs=logs, live=live, embedder=embedder)
    threads = Threads(serving, sockets)
    await threads.loaded()
    await threads.start()
    stack.push_async_callback(threads.closed)
    roster = Roster(connections.signal)
    await roster.start()
    stack.push_async_callback(roster.close)
    paced = Window(signal=connections.signal)
    await paced.start()
    stack.push_async_callback(paced.close)
    outbox = Outbox(connections, _box_mailbox(settings))
    stack.push_async_callback(outbox.drained)
    remembered = RememberedKeys(connections.pool, connections.signal)
    await remembered.start()
    stack.push_async_callback(remembered.close)
    return Gateway(
        connections=connections,
        logs=logs,
        sockets=sockets,
        live=live,
        roster=roster,
        codes=codes,
        keys=remembered,
        signer=Signer(settings.livekit_api_key, settings.livekit_api_secret, settings.token_key),
        threads=threads,
        closing=asyncio.Event(),
        embedder=embedder,
        signins=SignIns.kept(Words(connections.pool, connections.vault)),
        samples=Throttle(connections.pool, SAMPLES_A_MINUTE),
        paced=paced,
        outbox=outbox,
        evals=Runner(connections.pool),
    )


# Set from the signal handler, through the loop: streams never end on their own, and a stop
# would otherwise wait out the grace period and cut them mid-frame.
def announce_closing(fastapi_app: FastAPI) -> None:
    """Tell every open stream the process is stopping."""
    gateway: object = getattr(fastapi_app.state, "gateway", None)
    if isinstance(gateway, Gateway):
        asyncio.get_running_loop().call_soon_threadsafe(gateway.closing.set)


async def refused(_request: Request, error: Exception) -> Response:
    """A refusal answered with its status and its sentence."""
    status = error.status if isinstance(error, PinecallError) else 500
    headers = {"WWW-Authenticate": "Bearer"} if isinstance(error, NotSignedIn) else None
    if isinstance(error, Throttled):
        headers = {"Retry-After": str(error.retry_after_s)}
    return JSONResponse({"detail": str(error)}, status_code=status, headers=headers)


async def busy(request: Request, error: Exception) -> Response:
    """A full pool or a statement past its timeout, answered 503 with its one sentence."""
    if isinstance(error, PoolTimeout):
        return await refused(request, StoreUnreachable(NO_CONNECTION.format(wait=TIMEOUTS.wait_s)))
    took = TIMEOUTS.statement_ms / 1000
    return await refused(request, StoreUnreachable(TOO_SLOW.format(took=took)))


def origins_allowed(settings: Settings) -> tuple[str, ...]:
    """The app's two WebViews, then PINECALL_APP_ORIGINS, each once."""
    named = (item.strip() for item in settings.app_origins.split(","))
    return tuple(dict.fromkeys((*THE_APPS_WEBVIEWS, *(item for item in named if item))))


def widget_file(file: str) -> FileResponse:
    """A file of the widget, for any site to load as a module."""
    root = (BUILT / "widget").resolve()
    params = (root / file).resolve()
    if root not in params.parents or not params.is_file():
        raise HTTPException(404, "Not Found")
    return FileResponse(params, headers=WIDGET_HEADERS)


# The last route: a built file is itself, any other path is the page, so a reload lands where it
# was (the page reads its world from the path: `/sandbox/…` is the sandbox's). Never cached: a
# rebuild names new hashed assets in a new index.html.
def console(path: str) -> Response:
    """The console: an asset of its build, or its page."""
    if path.startswith(API_PREFIXES):
        raise HTTPException(404, "Not Found")
    root = (BUILT / "console").resolve()
    if not (root / THE_PAGE).is_file():
        raise HTTPException(404, NOT_BUILT)
    params = (root / path).resolve()
    if path and root in params.parents and params.is_file() and params.name != THE_PAGE:
        return FileResponse(params, headers=PAGE_HEADERS)
    page = (root / THE_PAGE).read_text(encoding="utf-8")
    return HTMLResponse(page, headers={**PAGE_HEADERS, "cache-control": "no-store"})


# One gateway's app: a test serves two, each with a gateway of its own in its state. Swagger under
# /v1: the console has a /docs screen of its own. The console is the last route: a built file is
# itself, any other path is the page.
def served_app() -> FastAPI:
    """The gateway's doors, its refusals, the widget and the console, on a new app."""
    served = FastAPI(
        title="Pinecall gateway", lifespan=lifespan, docs_url="/v1/docs", redoc_url="/v1/redoc"
    )
    served.add_middleware(AppOrigins)
    served.add_middleware(BodyCeiling)
    for doors in ROUTERS:
        served.include_router(doors.router)
    served.add_exception_handler(PinecallError, refused)
    served.add_exception_handler(PoolTimeout, busy)
    served.add_exception_handler(QueryCanceled, busy)
    served.add_api_route("/widget/{file}", widget_file, methods=["GET"], include_in_schema=False)
    served.add_api_route("/{path:path}", console, methods=["GET"], include_in_schema=False)
    return served


# A box with no embedder still starts: every door that embeds says what the operator must set.
async def embedder_of(connections: Connections) -> Embedder | None:
    """The box's embedder from its providers row and key; None, and said, when it has none."""
    try:
        embedding = (await catalog.providers(connections.pool)).embedding
    except NotAvailable:
        logger.warning(NO_EMBEDDER)
        return None
    if embedding is None:
        return None
    key = (await box_credentials(connections.pool, connections.vault)).get(embedding.vendor)
    if key is None:
        logger.warning(NO_EMBEDDER_KEY, embedding.vendor)
        return None
    return Embedder(embedding, key, connections.http)


# A box without mail still starts: its letters are not sent, and /.well-known says so.
def _box_mailbox(settings: Settings) -> Mailbox | None:
    if settings.smtp_url is None:
        return None
    try:
        return parse_mailbox_url(settings.smtp_url, settings.mail_from or "")
    except DeclarationRefused as unreadable:
        logger.warning(NO_BOX_MAIL, unreadable)
        return None


async def _cancelled[T](task: asyncio.Task[T]) -> None:
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


app = served_app()
