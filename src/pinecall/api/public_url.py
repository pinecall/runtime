"""The name this gateway is known by from outside: what a redirect URI and a link are built on."""

from __future__ import annotations

from fastapi import Request

from pinecall._settings import Settings


# The box's public name when it has one — a carrier already needs it, and it is what Caddy answers
# to — and what this request arrived at when it has not, which is a laptop on 8080. Never a header
# a caller sent: a redirect URI is compared byte for byte at an identity provider, and a link in a
# letter is a link somebody types a password into, so one a stranger could move would be a
# sign-in they could redirect to themselves.
def where_this_gateway_answers(settings: Settings, request: Request) -> str:
    """The base every outward-facing URL of this runtime is written from, with no trailing slash."""
    if settings.domain:
        return f"https://{settings.domain}"
    return str(request.base_url).rstrip("/")
