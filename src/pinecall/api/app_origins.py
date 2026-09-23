"""The origins Pinecall's own mobile app calls the /v1 doors from, and the CORS answer they get."""

from __future__ import annotations

from starlette.datastructures import Headers
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Receive, Scope, Send

from pinecall._settings import Settings
from pinecall.api._deps import a_settings
from pinecall.extensions.loading import named_in

# The app is a WebView, and a WebView's origin is not this box's: Capacitor serves iOS from
# `capacitor://localhost` and Android from `https://localhost`. Every build of the app has exactly
# these two, so they are code and not a setting. docs/protocol/people.md.
THE_APPS_WEBVIEWS = ("capacitor://localhost", "https://localhost")
DOORS = "/v1/"
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
# What the app sends: the bearer, a JSON body, the world and the corner it asks for, and the two
# an SSE reader resumes with. Nothing here is a cookie, so no answer says Allow-Credentials.
HEADERS = (
    "authorization",
    "content-type",
    "pinecall-env",
    "pinecall-corner",
    "last-event-id",
    "accept",
)
# How long a browser may keep a preflight's yes: ten minutes, one per door per session in practice.
MAX_AGE_S = 600


def origins_allowed(settings: Settings) -> tuple[str, ...]:
    """The app's two WebViews, then every origin PINECALL_APP_ORIGINS names, each once."""
    return tuple(dict.fromkeys(THE_APPS_WEBVIEWS + named_in(settings.app_origins)))


# Starlette's own CORSMiddleware says every header, and this only decides who reaches it: a /v1
# request from an origin on the list. Everything else passes as if there were no middleware at
# all — a socket, the widget (whose `*` is api/pages.py's), the console, and above all an origin
# NOT on the list, which gets no CORS header of any kind, preflight included, exactly as before.
# The list is read per request because the settings are the lifespan's, which runs after this
# middleware is built.
class AppOrigins:
    """CORS on the /v1 doors for the allowed origins, and for nobody else, on nothing else."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Hand an allowed origin's /v1 request to CORSMiddleware, and any other to the app."""
        if scope["type"] != "http" or not str(scope["path"]).startswith(DOORS):
            await self.app(scope, receive, send)
            return
        origin = Headers(scope=scope).get("origin")
        allowed = origins_allowed(a_settings(HTTPConnection(scope))) if origin else ()
        if origin not in allowed:
            await self.app(scope, receive, send)
            return
        cors = CORSMiddleware(
            self.app,
            allow_origins=allowed,
            allow_methods=METHODS,
            allow_headers=HEADERS,
            max_age=MAX_AGE_S,
        )
        await cors(scope, receive, send)
