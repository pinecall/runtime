"""How the doctor touches the world: one HTTP GET, one Postgres query, and the PATH."""

import asyncio
import shutil
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from pinecall.log.store.postgres import installed_extensions

# A doctor runs while something is broken: long enough for a healthy service on the same box,
# short enough that four checks against a dead one still answer in one breath.
TIMEOUT_SECONDS = 2.0


@dataclass(frozen=True)
class Probes:
    """The three ways the doctor leaves the process. The CLI passes live_probes(); a test fakes."""

    http_status: Callable[[str], int]
    postgres_extensions: Callable[[str], set[str]]
    executable_path: Callable[[str], str | None]


def live_probes() -> Probes:
    """The real ones: httpx, the store adapter that owns the driver, and the PATH."""
    return Probes(
        http_status=read_http_status,
        postgres_extensions=read_postgres_extensions,
        executable_path=read_executable_path,
    )


def read_http_status(url: str) -> int:
    """GET, and answer with the status. Whatever stops the request raises — that IS the reason."""
    return httpx.get(url, timeout=TIMEOUT_SECONDS).status_code


def read_postgres_extensions(dsn: str) -> set[str]:
    """Which extensions the database has. Synchronous: the CLI runs no event loop of its own."""
    return asyncio.run(installed_extensions(dsn, timeout=TIMEOUT_SECONDS))


# Looked for, never run: a doctor that executes a tool to learn its version is a doctor that can
# hang on it, and nothing in this tree shells out to `lk` at all.
def read_executable_path(program: str) -> str | None:
    """Where a program is on the PATH, or None when it is not installed."""
    return shutil.which(program)
