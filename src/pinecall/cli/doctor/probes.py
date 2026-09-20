"""How the doctor touches the world: an HTTP GET, a knock with a key, Postgres, the PATH."""

import asyncio
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass

import httpx

from pinecall._settings import Settings
from pinecall.log.store import StoreUnreachable, open_pool
from pinecall.log.store.postgres import installed_extensions
from pinecall.mail import BoxMail, TheBoxsMail
from pinecall.orgs.box import box_settings_for

# A doctor runs while something is broken: long enough for a healthy service on the same box,
# short enough that four checks against a dead one still answer in one breath.
TIMEOUT_SECONDS = 2.0
# A vendor is across the internet, not on the box; a key that answers in five seconds is alive.
KNOCK_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class Probes:
    """The five ways the doctor leaves the process. The CLI passes live_probes(); a test fakes."""

    http_status: Callable[[str], int]
    knock: Callable[[str, Mapping[str, str]], int]
    postgres_extensions: Callable[[str], set[str]]
    executable_path: Callable[[str], str | None]
    # The mail server the box posts through, read as the gateway reads it: what the operator
    # stored from the console, else the environment's. A test's default is a box that stored none.
    the_boxs_mail: Callable[[Settings], BoxMail | None] = lambda settings: (  # noqa: ARG005
        None
    )


def live_probes() -> Probes:
    """The real ones: httpx, twice, the store adapter that owns the driver, the PATH, the table."""
    return Probes(
        http_status=read_http_status,
        knock=read_knock,
        postgres_extensions=read_postgres_extensions,
        executable_path=read_executable_path,
        the_boxs_mail=read_the_boxs_mail,
    )


def read_http_status(url: str) -> int:
    """GET, and answer with the status. Whatever stops the request raises — that IS the reason."""
    return httpx.get(url, timeout=TIMEOUT_SECONDS).status_code


def read_knock(url: str, headers: Mapping[str, str]) -> int:
    """GET with a key in the headers, and answer with the status: 200 is alive, 401 is dead."""
    return httpx.get(url, headers=dict(headers), timeout=KNOCK_TIMEOUT_SECONDS).status_code


def read_postgres_extensions(dsn: str) -> set[str]:
    """Which extensions the database has. Synchronous: the CLI runs no event loop of its own."""
    return asyncio.run(installed_extensions(dsn, timeout=TIMEOUT_SECONDS))


# Looked for, never run: a doctor that executes a tool to learn its version is a doctor that can
# hang on it, and nothing in this tree shells out to `lk` at all.
def read_executable_path(program: str) -> str | None:
    """Where a program is on the PATH, or None when it is not installed."""
    return shutil.which(program)


# The same answer the gateway gives (mail/box.py): the stored row when there is one this box's
# vault key opens, else the environment's. A database that does not answer, or one still behind
# 0035, is a box with no stored row — the environment's line then stands, exactly as it does for
# a gateway that started against that database.
def read_the_boxs_mail(settings: Settings) -> BoxMail | None:
    """What this box posts a letter through, from the table first. Synchronous: no loop here."""
    return asyncio.run(_the_boxs_mail(settings))


async def _the_boxs_mail(settings: Settings) -> BoxMail | None:
    """One pool, one read, closed again."""
    from pinecall.mail.box import the_environments_mailbox

    environment = the_environments_mailbox(settings)
    try:
        pool = await open_pool(settings.database_url)
    except StoreUnreachable:
        return await TheBoxsMail(environment, None).of()
    try:
        return await TheBoxsMail(environment, box_settings_for(settings, pool)).of()
    except Exception:  # noqa: BLE001 — a table not there yet is the environment's turn
        return await TheBoxsMail(environment, None).of()
    finally:
        await pool.close()
