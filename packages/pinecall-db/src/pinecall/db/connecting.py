"""The database, reached: a DSN said without its password, a schema checked, a pool opened."""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, cast
from urllib.parse import urlsplit, urlunsplit

import asyncpg  # type: ignore[import-untyped]  # pyright: ignore[reportMissingTypeStubs]

from pinecall.db.pool import Pool
from pinecall.errors import PinecallError

# A schema name reaches SQL as an identifier, where no parameter can go. So it is checked here
# rather than quoted there: a name that is not a plain lowercase word never becomes SQL at all.
_A_SCHEMA_NAME = re.compile(r"^[a-z_][a-z0-9_]*$")

# Postgres's default schema, spelled once: a store may be pointed at another (the tests own one
# each), and nothing else in the runtime needs to know what the default is called.
DEFAULT_SCHEMA = "public"

INSTALLED_EXTENSIONS = "select extname from pg_extension"

# asyncpg ships no py.typed, so a strict checker reads every call into it as Unknown. Two casts at
# the door keep the rest of this package typed, and nothing untyped leaves a function.
_create_pool = cast(
    "Callable[..., Awaitable[Any]]",
    asyncpg.create_pool,  # pyright: ignore[reportUnknownMemberType]
)
connect = cast(
    "Callable[..., Awaitable[Any]]",
    asyncpg.connect,  # pyright: ignore[reportUnknownMemberType]
)


class SchemaNameRefused(PinecallError):
    """A schema name that is not a plain lowercase word. It would have been spliced into SQL."""


# Every way a database can fail to open, under one name, raised from the one package that may say
# the driver's. A caller deciding whether to fall back must not have to import asyncpg to ask.
class StoreUnreachable(PinecallError):
    """The database did not answer, or answered that this is not a database we can use."""


# A DSN carries the password, and this sentence is printed in a terminal, a journal and an issue:
# `migrate` printed `postgresql://pinecall:pinecall@…` at a person the day the box's password
# changed (2026-09-20). Every refusal that names the database names it through here.
def without_password(dsn: str) -> str:
    """The DSN as it may be shown: the user, the host, the database — never the password."""
    parts = urlsplit(dsn)
    if parts.password is None:
        return dsn
    host = _bracketed(parts.hostname or "")
    if parts.port is not None:
        host = f"{host}:{parts.port}"
    netloc = f"{parts.username}@{host}" if parts.username else host
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _bracketed(host: str) -> str:
    """urlsplit hands back an IPv6 host without its brackets, and `::1:5432` is not an address."""
    return f"[{host}]" if ":" in host else host


async def installed_extensions(dsn: str, *, timeout: float | None = None) -> set[str]:
    """Which extensions this database has. One connection, one query, closed either way."""
    connection: Any = await connect(dsn, timeout=timeout)
    try:
        rows: Sequence[Any] = await connection.fetch(INSTALLED_EXTENSIONS)
    finally:
        await connection.close()
    return {str(row["extname"]) for row in rows}


# Every pool of the runtime is opened here — the gateway's, a store's own, a CLI verb's — because
# this package is the one place that may name the driver, and a second door to it is a second one
# to close. `jsonb_as_dicts` is the log's: its rows go out and come back as dicts, never strings.
async def create_pool(
    dsn: str,
    *,
    schema: str = DEFAULT_SCHEMA,
    jsonb_as_dicts: bool = False,
    min_size: int | None = None,
    max_size: int | None = None,
) -> Pool:
    """A pool in this schema, or StoreUnreachable naming the database it could not open."""
    options: dict[str, Any] = {"server_settings": {"search_path": search_path_of(schema)}}
    if jsonb_as_dicts:
        options["init"] = _teach_the_connection_json
    if min_size is not None:
        options["min_size"] = min_size
    if max_size is not None:
        options["max_size"] = max_size
    try:
        # The driver's pool answers our Protocol; the driver types it as nothing at all.
        return cast("Pool", await _create_pool(dsn, **options))
    except (OSError, ValueError, asyncpg.PostgresError) as refused:
        raise StoreUnreachable(f"{without_password(dsn)}: {refused}") from refused


async def _teach_the_connection_json(connection: Any) -> None:
    """jsonb comes back as a dict and goes out as one; the store never sees a JSON string."""
    await connection.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )


# A schema of its own is what a test process gets, and its tables land there. The extensions'
# types and operators — halfvec, <=>, bm25's <@> — live where CREATE EXTENSION put them, in
# public, and a path that hides public cannot name a column of that type. So the schema comes
# first, where DDL creates, and public after it, where the types are found. The default schema is
# public itself and needs no second entry.
def search_path_of(schema: str) -> str:
    """The search path a schema is worked in: itself, then public, where the extensions are."""
    name = check_schema_name(schema)
    return name if name == DEFAULT_SCHEMA else f"{name}, {DEFAULT_SCHEMA}"


def check_schema_name(schema: str) -> str:
    """A schema is an identifier and cannot be a parameter, so it is checked before it is SQL."""
    if not _A_SCHEMA_NAME.match(schema):
        raise SchemaNameRefused(f"a schema name is a lowercase word, not {schema!r}")
    return schema


async def open_pool(database_url: str, *, schema: str = DEFAULT_SCHEMA) -> Pool:
    """The pool the gateway holds for its whole life. The lifespan that opened it closes it."""
    return await create_pool(database_url, schema=schema)
