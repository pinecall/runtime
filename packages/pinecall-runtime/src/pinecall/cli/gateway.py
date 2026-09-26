"""`pinecall-runtime gateway`: the control plane, one uvicorn process over pinecall.api."""

import argparse
import ipaddress
from typing import Any
from urllib.parse import urlsplit

import uvicorn
from livekit.agents.cli.log import JsonFormatter

from pinecall._settings import Settings, load_settings, variable_of
from pinecall.errors import PinecallError

PURPOSE: str = "the control plane: HTTP and WebSocket, one process"

# The import string, not the object: uvicorn's reloader has to be able to import it again.
APP = "pinecall.api.app:app"

# A stop waits this long for the requests in flight and then closes what is left — the log streams
# and the app sockets, which reconnect. The gateway drains no call: every live call is told again by
# its worker and adopted by its app's socket when this process, or the next, answers.
GRACEFUL_S = 5

# The gateway binds the address its own instance is reached at — PINECALL_GATEWAY_URL, which the
# worker, the key units and Caddy's site read too — so a second instance on one box is ONE variable
# in its env file and never a port said twice. It stays on loopback: Caddy is what the world
# reaches, and a URL that names another host is a worker box's (the hub, far away), not a gateway's.
NOT_HERE = (
    "{variable} is {url}: a gateway binds a loopback address with a port it names "
    "(http://127.0.0.1:8080), or is told --host and --port"
)


# One line of the gateway in a terminal. The json line is livekit's own JsonFormatter, the one
# the worker's `start` verb writes by itself, so one journal holds one shape.
TEXT_LINE = "%(asctime)s %(levelname)-7s %(name)s %(message)s"

# Loggers that say every request at INFO: the gateway makes one per lookup, push and heartbeat.
QUIET_AT_INFO = ("httpx", "httpcore")


class NotBindable(PinecallError):
    """PINECALL_GATEWAY_URL names no loopback host and port this process could listen on."""


def configure(parser: argparse.ArgumentParser) -> None:
    """No verbs: the gateway is one process, and its flags arrive with the process."""
    parser.add_argument("--host", help="default: the host of PINECALL_GATEWAY_URL")
    parser.add_argument("--port", type=int, help="default: the port of PINECALL_GATEWAY_URL")
    parser.add_argument("--reload", action="store_true", help="restart on a source change")
    parser.set_defaults(run=run)


def run(arguments: argparse.Namespace) -> int:
    """Hand the process to uvicorn. It returns when the server stops, which is the exit code."""
    settings = load_settings()
    host, port = arguments.host, arguments.port
    if host is None or port is None:
        own_host, own_port = bind_address(settings.gateway_url)
        host, port = host or own_host, port or own_port
    uvicorn.run(
        APP,
        host=host,
        port=port,
        reload=arguments.reload,
        log_level=settings.log_level.lower(),
        log_config=build_log_config(settings),
        timeout_graceful_shutdown=GRACEFUL_S,
    )
    return 0


# Handed to uvicorn rather than applied here: uvicorn runs dictConfig itself in the process that
# serves, which under --reload is one it spawns — a config applied in this process alone left
# that child with Python's last-resort handler, WARNING and up, and nothing else.
def build_log_config(settings: Settings) -> dict[str, Any]:
    """logging's dictConfig for the gateway: every logger to stdout, at PINECALL_LOG_LEVEL."""
    line: dict[str, Any] = (
        {"()": JsonFormatter}
        if settings.log_format == "json"
        else {"format": TEXT_LINE, "datefmt": "%H:%M:%S"}
    )
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"line": line},
        "handlers": {
            "stdout": {
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
                "formatter": "line",
            }
        },
        "root": {"level": settings.log_level.upper(), "handlers": ["stdout"]},
        "loggers": {name: {"level": "WARNING"} for name in QUIET_AT_INFO},
    }


def bind_address(url: str) -> tuple[str, int]:
    """The loopback host and the port a gateway URL names; the refusal when it names neither."""
    parts = urlsplit(url)
    refused = NotBindable(NOT_HERE.format(variable=variable_of("gateway_url"), url=url))
    try:
        port = parts.port
    except ValueError:
        raise refused from None
    if port is None or not _is_loopback(parts.hostname):
        raise refused
    return str(parts.hostname), port


def _is_loopback(host: str | None) -> bool:
    """127.0.0.0/8, ::1 and the name that means them; anything else is another machine's."""
    if host == "localhost":
        return True
    try:
        return host is not None and ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
