"""What the box hosts for an org: an app with the token its process runs on, and its releases."""

import hashlib
import io
import re
import tarfile
from dataclasses import dataclass
from datetime import datetime

from cryptography.fernet import MultiFernet
from psycopg.rows import DictRow

from pinecall.domain.errors import DeclarationRefused, NotFound
from pinecall.domain.names import Env, parse_slug
from pinecall.domain.person import SERVER_SCOPES
from pinecall.postgres.pool import Pool
from pinecall.tenancy import keys
from pinecall.tenancy.people import fingerprint
from pinecall.tenancy.vault import opened, sealed

# The ceilings of one upload: what travels, what it unpacks to, and how many files.
LARGEST_SOURCE = 10 * 1024 * 1024


LARGEST_UNPACKED = 100 * 1024 * 1024


MOST_FILES = 5000


# A host name is 63 characters at most, and an app's name is part of it whole.
LONGEST_NAME = 40


STAMP = 8


# How long after a person asks for an app's logs the runner is told to send them.
LOGS_FOR_S = 60


TOO_BIG = "a release's sources are {limit} MB at most packed"


TOO_LONG_A_NAME = "an app's name is {limit} characters at most, and {name} is {length}"


NOT_A_TARBALL = "a release is a gzipped tarball of the project's sources, and this is not one"


UNPACKS_TOO_BIG = "a release unpacks to {limit} MB at most, and this one passes it"


TOO_MANY_FILES = "a release holds {limit} files at most: leave node_modules and build output out"


NOT_A_FILE = "{path} is a link or a device: a release holds files and folders only"


LEAVES_THE_PROJECT = "{path} leaves the project's folder: every path of a release stays inside it"


NOT_HOSTED = "the box hosts no app called {name} for this org in {env}"


NO_RELEASE = "{name} has no release {release}"


LABEL = "hosted app {name}"


# One row per app with a release: the newest, and a stamp of the org's secrets in that world,
# which is what its host name is made of. An org's list leaves its other orgs out.
STATUS = """
SELECT app.org, app.env, app.name, app.created_by, app.created_at, app.live_release,
       app.live_host, app.failed_host, app.failed_why, newest.release, newest.sha256,
       app.stopped_at,
       app.logs_asked_at > now() - make_interval(secs => %(logs_for_s)s) AS logs_wanted,
       (SELECT coalesce(string_agg(name || '@' || extract(epoch FROM set_at), ','
                                   ORDER BY name), '')
        FROM org_secrets WHERE org = app.org AND env = app.env) AS secrets
FROM hosted_apps app
LEFT JOIN LATERAL (
    SELECT release, sha256 FROM hosted_releases
    WHERE org = app.org AND env = app.env AND name = app.name ORDER BY release DESC LIMIT 1
) newest ON true
WHERE app.env = %(env)s AND (%(org)s::text IS NULL OR app.org = %(org)s)
ORDER BY app.org, app.name
"""


IS_HOSTED = "SELECT 1 FROM hosted_apps WHERE org = %(org)s AND env = %(env)s AND name = %(name)s"


# Two uploads of a new app at once: the second insert writes nothing and its token is revoked.
OPEN_APP = """
INSERT INTO hosted_apps (org, env, name, key_fingerprint, sealed_key, created_by)
VALUES (%(org)s, %(env)s, %(name)s, %(key_fingerprint)s, %(sealed_key)s, %(created_by)s)
ON CONFLICT (org, env, name) DO NOTHING RETURNING name
"""


# The app's row is locked first, so two uploads at once are two releases, never one number twice.
LOCK_APP = """
SELECT 1 FROM hosted_apps WHERE org = %(org)s AND env = %(env)s AND name = %(name)s FOR UPDATE
"""


KEEP_RELEASE = """
INSERT INTO hosted_releases (org, env, name, release, source, sha256, bytes, author, note)
SELECT %(org)s, %(env)s, %(name)s, coalesce(max(release), 0) + 1, %(source)s, %(sha256)s,
       %(bytes)s, %(author)s, %(note)s
FROM hosted_releases WHERE org = %(org)s AND env = %(env)s AND name = %(name)s
RETURNING release, sha256, bytes, author, note, created_at
"""


RELEASES = """
SELECT release, sha256, bytes, author, note, created_at FROM hosted_releases
WHERE org = %(org)s AND env = %(env)s AND name = %(name)s ORDER BY release DESC
"""


SOURCE = """
SELECT source, sha256 FROM hosted_releases
WHERE org = %(org)s AND env = %(env)s AND name = %(name)s AND release = %(release)s
"""


SEALED_KEY = """
SELECT sealed_key FROM hosted_apps WHERE org = %(org)s AND env = %(env)s AND name = %(name)s
"""


WENT_LIVE = """
UPDATE hosted_apps SET live_release = %(release)s, live_host = %(host)s,
       reported_by = %(runner)s, reported_at = now()
WHERE org = %(org)s AND env = %(env)s AND name = %(name)s
"""


FAILED = """
UPDATE hosted_apps SET failed_host = %(host)s, failed_why = %(why)s,
       reported_by = %(runner)s, reported_at = now()
WHERE org = %(org)s AND env = %(env)s AND name = %(name)s
"""


DROP_APP = """
DELETE FROM hosted_apps WHERE org = %(org)s AND env = %(env)s AND name = %(name)s
RETURNING key_fingerprint
"""


@dataclass(frozen=True)
class HostedApp:
    """Which app: the org it is hosted for, the world it runs in, and its name."""

    org: str
    env: Env
    name: str

    @property
    def columns(self) -> dict[str, str]:
        """The three columns every row of the app carries."""
        return {"org": self.org, "env": self.env, "name": self.name}


@dataclass(frozen=True)
class AppStatus:
    """A hosted app as the runner left it: its newest release, what serves it, and what failed."""

    org: str
    name: str
    created_by: str
    created_at: datetime
    # None before the first release; the rest of the newest release is None with it.
    release: int | None
    sha256: str | None
    # The name the release's process runs under: one release under one set of secrets.
    host: str | None
    live_release: int | None
    # Why the wanted host failed to build or start; None when it did not, or was never tried.
    failed_why: str | None
    # A person stopped it: the runner is not told to run it, and its releases stay.
    stopped: bool = False


@dataclass(frozen=True)
class Runnable:
    """An app a runner is to run: its newest release, the host it runs under, and what serves."""

    org: str
    name: str
    release: int
    sha256: str
    host: str
    # The host was reported failed: the runner leaves it alone.
    failed: bool
    logs_wanted: bool
    live_host: str | None


@dataclass(frozen=True)
class Source:
    """One upload, checked: the tarball as it came, and its sha256."""

    data: bytes
    sha256: str


@dataclass(frozen=True)
class Release:
    """One upload of an app's sources as it is kept: its number, and who sent it and why."""

    release: int
    sha256: str
    bytes: int
    author: str
    note: str
    created_at: datetime


# <name>-r<release>-<stamp>: the release is read back off it, so a report names nothing else.
A_HOST = re.compile(rf"^(?P<name>[a-z0-9-]+)-r(?P<release>[1-9][0-9]*)-[0-9a-f]{{{STAMP}}}$")


def checked_name(name: str) -> str:
    """The name as an app's: a slug short enough to be part of a host name."""
    if len(parse_slug(name)) > LONGEST_NAME:
        refusal = TOO_LONG_A_NAME.format(limit=LONGEST_NAME, name=name, length=len(name))
        raise DeclarationRefused(refusal)
    return name


# What the process calls its machine, which is what the gateway's list of processes shows, and the
# name of its container on a machine every org's apps share: the stamp is of the org, the world and
# the app too, so two orgs' apps of one name and one source never meet, and of the org's secrets,
# so a changed secret is a new host.
def host_of(app: HostedApp, release: int, sha256: str, secrets: str) -> str:
    """The host name one release of the app runs under, with the org's secrets as they are."""
    stamped = f"{app.org}:{app.env}:{app.name}:{release}:{sha256}:{secrets}"
    return f"{app.name}-r{release}-{hashlib.sha256(stamped.encode()).hexdigest()[:STAMP]}"


def is_a_host_of(name: str, host: str) -> bool:
    """Whether the host is one a release of the app runs under, whichever release."""
    named = A_HOST.match(host)
    return named is not None and named["name"] == name


def release_of(host: str) -> int | None:
    """The release a host runs, read off its name; None for a name that is no host's."""
    named = A_HOST.match(host)
    return None if named is None else int(named["release"])


# Read here, before it is kept, so whoever unpacks a release later unpacks nothing but files
# and folders under the project's own folder.
def checked_source(data: bytes) -> Source:
    """The upload as a release's sources; DeclarationRefused for anything but a safe tarball."""
    if len(data) > LARGEST_SOURCE:
        raise DeclarationRefused(TOO_BIG.format(limit=LARGEST_SOURCE >> 20))
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tarball:
            _check_members(tarball)
    except (tarfile.TarError, OSError, EOFError) as unreadable:
        raise DeclarationRefused(NOT_A_TARBALL) from unreadable
    return Source(data=data, sha256=hashlib.sha256(data).hexdigest())


async def apps_of(pool: Pool, org: str, env: Env) -> list[AppStatus]:
    """The apps the box hosts for the org in the world, by name, stopped ones too."""
    return [_status(row) for row in await _rows(pool, org, env)]


# Every org's: what the world's runner is told to have running. A stopped app is left out, and a
# runner stops what it is not told to run; one with no release yet has nothing to run.
async def hosted_in(pool: Pool, env: Env) -> list[Runnable]:
    """Every app the box runs in the world, by org and name."""
    return [
        _runnable(row)
        for row in await _rows(pool, None, env)
        if row["stopped_at"] is None and row["release"] is not None
    ]


async def is_hosted(pool: Pool, app: HostedApp) -> bool:
    """Whether the box already hosts the app."""
    async with pool.connection() as connection:
        return await (await connection.execute(IS_HOSTED, app.columns)).fetchone() is not None


# The token is the org's and the world's, a server's: what `pinecall start` would be given by hand.
async def open_app(pool: Pool, vault: MultiFernet, app: HostedApp, *, created_by: str) -> None:
    """Start hosting the app: a server's token minted for its process, kept sealed."""
    issued = keys.Issued(
        org=app.org,
        env=app.env,
        scopes=SERVER_SCOPES,
        label=LABEL.format(name=app.name),
        created_by=created_by,
    )
    _, secret = await keys.issue(pool, issued)
    values = {
        **app.columns,
        "key_fingerprint": fingerprint(secret),
        "sealed_key": sealed(vault, secret),
        "created_by": created_by,
    }
    async with pool.connection() as connection:
        opened = await (await connection.execute(OPEN_APP, values)).fetchone()
    if opened is None:
        await keys.revoke(pool, fingerprint(secret))


async def keep_release(
    pool: Pool, app: HostedApp, source: Source, *, author: str, note: str
) -> Release:
    """Keep the upload as the app's next release; NotFound for an app the box does not host."""
    values = {
        **app.columns,
        "source": source.data,
        "sha256": source.sha256,
        "bytes": len(source.data),
        "author": author,
        "note": note,
    }
    async with pool.connection() as connection, connection.transaction():
        locked = await (await connection.execute(LOCK_APP, app.columns)).fetchone()
        kept = None if locked is None else await connection.execute(KEEP_RELEASE, values)
        row = None if kept is None else await kept.fetchone()
    if row is None:
        raise NotFound(NOT_HOSTED.format(name=app.name, env=app.env))
    return _release(row)


async def releases_of(pool: Pool, app: HostedApp) -> list[Release]:
    """The app's releases, newest first; NotFound for an app the box does not host."""
    # independent: the app looked for, then its releases read
    async with pool.connection() as connection:
        if await (await connection.execute(IS_HOSTED, app.columns)).fetchone() is None:
            raise NotFound(NOT_HOSTED.format(name=app.name, env=app.env))
        rows = await (await connection.execute(RELEASES, app.columns)).fetchall()
    return [_release(row) for row in rows]


# Checked when it was kept: what comes back is a release's sources as they are.
async def source_of(pool: Pool, app: HostedApp, release: int) -> Source:
    """The tarball one release was uploaded as; NotFound for a release nobody sent."""
    async with pool.connection() as connection:
        found = await connection.execute(SOURCE, {**app.columns, "release": release})
        row = await found.fetchone()
    if row is None:
        raise NotFound(NO_RELEASE.format(name=app.name, release=release))
    return Source(data=bytes(row["source"]), sha256=row["sha256"])


async def key_of(pool: Pool, vault: MultiFernet, app: HostedApp) -> str:
    """The server's token the app's process runs on, opened; NotFound for an app not hosted."""
    async with pool.connection() as connection:
        row = await (await connection.execute(SEALED_KEY, app.columns)).fetchone()
    secret = None if row is None else opened(vault, row["sealed_key"])
    if not isinstance(secret, str):
        raise NotFound(NOT_HOSTED.format(name=app.name, env=app.env))
    return secret


# A report names its host, and a host names its release: one about a release since replaced is
# kept as what it is, and says nothing of the release wanted now.
async def went_live(pool: Pool, app: HostedApp, host: str, *, runner: str) -> None:
    """Record that the host serves the app now, and with it the release it runs."""
    values = {**app.columns, "release": release_of(host), "host": host, "runner": runner}
    async with pool.connection() as connection:
        await connection.execute(WENT_LIVE, values)


async def failed(pool: Pool, app: HostedApp, host: str, *, why: str, runner: str) -> None:
    """Record that the host did not build, start or stay up, and why."""
    values = {**app.columns, "host": host, "why": why, "runner": runner}
    async with pool.connection() as connection:
        await connection.execute(FAILED, values)


async def drop_app(pool: Pool, app: HostedApp) -> None:
    """Stop hosting the app: its releases go, and its token opens nothing from the next request."""
    async with pool.connection() as connection:
        row = await (await connection.execute(DROP_APP, app.columns)).fetchone()
    if row is None:
        raise NotFound(NOT_HOSTED.format(name=app.name, env=app.env))
    await keys.revoke(pool, row["key_fingerprint"])


async def _rows(pool: Pool, org: str | None, env: Env) -> list[DictRow]:
    values = {"org": org, "env": env, "logs_for_s": LOGS_FOR_S}
    async with pool.connection() as connection:
        return await (await connection.execute(STATUS, values)).fetchall()


def _check_members(tarball: tarfile.TarFile) -> None:
    unpacked = 0
    for files, member in enumerate(tarball, start=1):
        path = member.name
        if not (member.isfile() or member.isdir()):
            raise DeclarationRefused(NOT_A_FILE.format(path=path))
        if path.startswith("/") or ".." in path.split("/"):
            raise DeclarationRefused(LEAVES_THE_PROJECT.format(path=path))
        unpacked += member.size
        if files > MOST_FILES:
            raise DeclarationRefused(TOO_MANY_FILES.format(limit=MOST_FILES))
        if unpacked > LARGEST_UNPACKED:
            raise DeclarationRefused(UNPACKS_TOO_BIG.format(limit=LARGEST_UNPACKED >> 20))


def _status(row: DictRow) -> AppStatus:
    host = None if row["release"] is None else _host(row)
    return AppStatus(
        org=row["org"],
        name=row["name"],
        created_by=row["created_by"],
        created_at=row["created_at"],
        release=row["release"],
        sha256=row["sha256"],
        host=host,
        live_release=row["live_release"],
        failed_why=row["failed_why"] if host is not None and row["failed_host"] == host else None,
        stopped=row["stopped_at"] is not None,
    )


def _runnable(row: DictRow) -> Runnable:
    host = _host(row)
    return Runnable(
        org=row["org"],
        name=row["name"],
        release=row["release"],
        sha256=row["sha256"],
        host=host,
        failed=row["failed_host"] == host,
        logs_wanted=bool(row["logs_wanted"]),
        live_host=row["live_host"],
    )


def _host(row: DictRow) -> str:
    app = HostedApp(org=row["org"], env=row["env"], name=row["name"])
    return host_of(app, row["release"], row["sha256"], row["secrets"])


def _release(row: DictRow) -> Release:
    return Release(
        release=row["release"],
        sha256=row["sha256"],
        bytes=row["bytes"],
        author=row["author"],
        note=row["note"],
        created_at=row["created_at"],
    )
