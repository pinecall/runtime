"""`pinecall-runtime gateway`: the control plane, one uvicorn process over pinecall.api."""

import argparse

import uvicorn

from pinecall._settings import load_settings
from pinecall.auth import dev_file

PURPOSE: str = "the control plane: HTTP and WebSocket, one process"

# The import string, not the object: uvicorn's reloader has to be able to import it again.
APP = "pinecall.api.app:app"
# Every interface: which of them the world reaches is the box's business, not the process's.
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8080


def configure(parser: argparse.ArgumentParser) -> None:
    """No verbs: the gateway is one process, and its flags arrive with the process."""
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"default {DEFAULT_HOST}")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"default {DEFAULT_PORT}")
    parser.add_argument("--reload", action="store_true", help="restart on a source change")
    parser.set_defaults(run=run)


def run(arguments: argparse.Namespace) -> int:
    """Hand the process to uvicorn. It returns when the server stops, which is the exit code."""
    settings = load_settings()
    # A gateway on a dev key leaves its door where the tenant's CLI looks, so a terminal on this
    # machine needs nothing exported at all. A box has no dev key and writes nothing.
    dev_file.written(settings.dev_key, arguments.port)
    uvicorn.run(
        APP,
        host=arguments.host,
        port=arguments.port,
        reload=arguments.reload,
        log_level=settings.log_level.lower(),
    )
    return 0
