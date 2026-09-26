"""How the doctor leaves the process: HTTP, a key, Postgres, the PATH, a word, the disk, TLS."""

import asyncio
import shutil
import socket
import ssl
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

from pinecall._settings import Settings
from pinecall.log.store import StoreUnreachable, open_pool
from pinecall.log.store.postgres import installed_extensions
from pinecall.mail import BoxMail, TheBoxsMail
from pinecall.orgs.box_settings import box_settings_for
from pinecall.providers.embed import embedder_for

# A doctor runs while something is broken: long enough for a healthy service on the same box,
# short enough that four checks against a dead one still answer in one breath.
TIMEOUT_SECONDS = 2.0
# A vendor is across the internet, not on the box; a key that answers in five seconds is alive.
KNOCK_TIMEOUT_SECONDS = 5.0

# The one word the doctor embeds to prove this box can embed at all. It is a real request with
# the real key at the real model, because that is the only thing that answers the question: the
# vendors' base URLs give the same status to a live key, a dead one and none.
A_WORD = "pinecall"


@dataclass(frozen=True)
class Probes:
    """Every way the doctor leaves the process. The CLI passes live_probes(); a test fakes."""

    http_status: Callable[[str], int]
    knock: Callable[[str, Mapping[str, str]], int]
    postgres_extensions: Callable[[str], set[str]]
    executable_path: Callable[[str], str | None]
    # One word through whichever embedder this box is configured with, answering how wide the
    # vector came back. Whatever stops it raises, and the sentence it raises with is the reason.
    embed_width: Callable[[Settings], int]
    # The mail server the box posts through, read as the gateway reads it: what the operator
    # stored from the console, else the environment's. A test's default is a box that stored none.
    the_boxs_mail: Callable[[Settings], BoxMail | None] = lambda settings: (  # noqa: ARG005
        None
    )
    # The machine itself: free gigabytes on the file system under a path, whether a systemd unit
    # is active (None where there is no systemd to ask), and when the certificate a name serves
    # ends. A test's defaults are a machine with room, a fence up and a year of certificate.
    disk_free_gb: Callable[[str], float] = lambda path: 100.0  # noqa: ARG005
    unit_active: Callable[[str], bool | None] = lambda unit: True  # noqa: ARG005
    certificate_expiry: Callable[[str], datetime] = lambda domain: (  # noqa: ARG005
        datetime(2100, 1, 1, tzinfo=UTC)
    )


def live_probes() -> Probes:
    """The real ones: httpx, twice, the store adapter that owns the driver, the PATH, the table."""
    return Probes(
        http_status=read_http_status,
        knock=read_knock,
        postgres_extensions=read_postgres_extensions,
        executable_path=read_executable_path,
        embed_width=read_embed_width,
        the_boxs_mail=read_the_boxs_mail,
        disk_free_gb=read_disk_free_gb,
        unit_active=read_unit_active,
        certificate_expiry=read_certificate_expiry,
    )


def read_http_status(url: str) -> int:
    """GET, and answer with the status. Whatever stops the request raises — that IS the reason."""
    return httpx.get(url, timeout=TIMEOUT_SECONDS).status_code


def read_knock(url: str, headers: Mapping[str, str]) -> int:
    """GET with a key in the headers, and answer with the status: 200 is alive, 401 is dead."""
    return httpx.get(url, headers=dict(headers), timeout=KNOCK_TIMEOUT_SECONDS).status_code


# The embedder this box is configured with, asked for real: the vendor, the model, the key, over
# HTTP. Synchronous like every other probe — the CLI runs no event loop of its own.
def read_embed_width(settings: Settings) -> int:
    """Embed one word and answer how wide the vector came back."""
    return asyncio.run(_embed_a_word(settings))


async def _embed_a_word(settings: Settings) -> int:
    """One client, one request, closed again."""
    async with httpx.AsyncClient(timeout=KNOCK_TIMEOUT_SECONDS) as http:
        vectors = await embedder_for(settings, http).embed([A_WORD])
    return len(vectors[0])


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
    except Exception:
        return await TheBoxsMail(environment, None).of()
    finally:
        await pool.close()


# The file system under a path that may not exist yet — an instance's recordings directory is
# made by its first call — so the nearest directory that does is the one measured.
def read_disk_free_gb(path: str) -> float:
    """Free gigabytes on the file system a path lands on."""
    where = Path(path).resolve()
    while not where.exists():
        where = where.parent
    return shutil.disk_usage(where).free / 1e9


# Asked, not run: `is-active` answers off the manager's state in milliseconds, as any user.
def read_unit_active(unit: str) -> bool | None:
    """Whether a systemd unit is active; None on a machine with no systemctl to ask."""
    systemctl = shutil.which("systemctl")
    if systemctl is None:
        return None
    asked = subprocess.run(  # noqa: S603 — a program found on the PATH, a unit's name, no shell
        [systemctl, "is-active", "--quiet", unit], check=False, timeout=TIMEOUT_SECONDS
    )
    return asked.returncode == 0


def read_certificate_expiry(domain: str) -> datetime:
    """When the certificate the domain serves on 443 ends, read off one TLS handshake."""
    context = ssl.create_default_context()
    with (
        socket.create_connection((domain, 443), timeout=KNOCK_TIMEOUT_SECONDS) as raw,
        context.wrap_socket(raw, server_hostname=domain) as tls,
    ):
        said = tls.getpeercert()
    ends = said.get("notAfter") if said else None
    if not isinstance(ends, str):
        raise ValueError(f"{domain} served no certificate")
    return datetime.fromtimestamp(ssl.cert_time_to_seconds(ends), UTC)
