"""What the box hosts for an org: an app with the token its process runs on, and its releases."""

import hashlib
import io
import tarfile
from dataclasses import dataclass
from datetime import datetime

from cryptography.fernet import MultiFernet
from psycopg.rows import DictRow

from pinecall.domain.errors import DeclarationRefused, NotFound
from pinecall.domain.names import Env
from pinecall.domain.person import SERVER_SCOPES
from pinecall.postgres.pool import Pool
from pinecall.tenancy import keys
from pinecall.tenancy.people import fingerprint
from pinecall.tenancy.vault import sealed

# The ceilings of one upload: what travels, what it unpacks to, and how many files.
LARGEST_SOURCE = 10 * 1024 * 1024


LARGEST_UNPACKED = 100 * 1024 * 1024


MOST_FILES = 5000


TOO_BIG = "a release's sources are {limit} MB at most packed, and this upload is {size:.1f} MB"


NOT_A_TARBALL = "a release is a gzipped tarball of the project's sources, and this is not one"


UNPACKS_TOO_BIG = "a release unpacks to {limit} MB at most, and this one passes it"


TOO_MANY_FILES = "a release holds {limit} files at most: leave node_modules and build output out"


NOT_A_FILE = "{path} is a link or a device: a release holds files and folders only"


LEAVES_THE_PROJECT = "{path} leaves the project's folder: every path of a release stays inside it"


NOT_HOSTED = "the box hosts no app called {name} for this org in {env}"


NO_RELEASE = "{name} has no release {release}"


LABEL = "hosted app {name}"


APPS = """
SELECT app.name, app.created_by, app.created_at,
       (SELECT max(release) FROM hosted_releases
        WHERE org = app.org AND env = app.env AND name = app.name) AS release
FROM hosted_apps app WHERE app.org = %(org)s AND app.env = %(env)s ORDER BY app.name
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
SELECT source FROM hosted_releases
WHERE org = %(org)s AND env = %(env)s AND name = %(name)s AND release = %(release)s
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
class ListedApp:
    """A hosted app as the org's list shows it: its newest release, none before the first."""

    name: str
    release: int | None
    created_by: str
    created_at: datetime


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


# Read here, before it is kept, so whoever unpacks a release later unpacks nothing but files
# and folders under the project's own folder.
def checked_source(data: bytes) -> Source:
    """The upload as a release's sources; DeclarationRefused for anything but a safe tarball."""
    if len(data) > LARGEST_SOURCE:
        megabytes = len(data) / (1024 * 1024)
        raise DeclarationRefused(TOO_BIG.format(limit=LARGEST_SOURCE >> 20, size=megabytes))
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tarball:
            _check_members(tarball)
    except (tarfile.TarError, OSError, EOFError) as unreadable:
        raise DeclarationRefused(NOT_A_TARBALL) from unreadable
    return Source(data=data, sha256=hashlib.sha256(data).hexdigest())


async def apps_of(pool: Pool, org: str, env: Env) -> list[ListedApp]:
    """The apps the box hosts for the org in the world, by name."""
    async with pool.connection() as connection:
        rows = await (await connection.execute(APPS, {"org": org, "env": env})).fetchall()
    return [
        ListedApp(
            name=row["name"],
            release=row["release"],
            created_by=row["created_by"],
            created_at=row["created_at"],
        )
        for row in rows
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
        if await (await connection.execute(LOCK_APP, app.columns)).fetchone() is None:
            raise NotFound(NOT_HOSTED.format(name=app.name, env=app.env))
        row = await (await connection.execute(KEEP_RELEASE, values)).fetchone()
    if row is None:
        raise NotFound(NOT_HOSTED.format(name=app.name, env=app.env))
    return _release(row)


async def releases_of(pool: Pool, app: HostedApp) -> list[Release]:
    """The app's releases, newest first; NotFound for an app the box does not host."""
    async with pool.connection() as connection:
        if await (await connection.execute(IS_HOSTED, app.columns)).fetchone() is None:
            raise NotFound(NOT_HOSTED.format(name=app.name, env=app.env))
        rows = await (await connection.execute(RELEASES, app.columns)).fetchall()
    return [_release(row) for row in rows]


async def source_of(pool: Pool, app: HostedApp, release: int) -> bytes:
    """The tarball one release was uploaded as; NotFound for a release nobody sent."""
    async with pool.connection() as connection:
        found = await connection.execute(SOURCE, {**app.columns, "release": release})
        row = await found.fetchone()
    if row is None:
        raise NotFound(NO_RELEASE.format(name=app.name, release=release))
    return bytes(row["source"])


async def drop_app(pool: Pool, app: HostedApp) -> None:
    """Stop hosting the app: its releases go, and its token opens nothing from the next request."""
    async with pool.connection() as connection:
        row = await (await connection.execute(DROP_APP, app.columns)).fetchone()
    if row is None:
        raise NotFound(NOT_HOSTED.format(name=app.name, env=app.env))
    await keys.revoke(pool, row["key_fingerprint"])


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


def _release(row: DictRow) -> Release:
    return Release(
        release=row["release"],
        sha256=row["sha256"],
        bytes=row["bytes"],
        author=row["author"],
        note=row["note"],
        created_at=row["created_at"],
    )
