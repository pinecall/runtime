"""The Postgres the log suite writes to: probed once, given its own schema, dropped at the end."""

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from itertools import count
from typing import Any, cast
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import asyncpg  # type: ignore[import-untyped]  # pyright: ignore[reportMissingTypeStubs]
import pytest

from pinecall._settings import load_settings
from pinecall.log.store import MemoryStore, PostgresStore, Store
from pinecall.log.store.migrating import apply_migrations
from pinecall.log.store.postgres import search_path_of

# Long enough for a container on the same laptop, short enough that a whole suite does not hang
# waiting for a database nobody started.
PROBE_TIMEOUT_SECONDS = 3.0

# asyncpg ships no py.typed; one cast at the door keeps the rest of this file typed.
_connect = cast("Any", asyncpg.connect)  # pyright: ignore[reportUnknownMemberType]


# Every test gets a clock of its own, ticking halves from 1.0, so an entry's ts is readable in a
# failure message and the two stores are compared on the same numbers.
def a_clock() -> Any:
    """The clock both stores take: 1.0, 1.5, 2.0 …"""
    return count(1.0, 0.5).__next__


@dataclass(frozen=True)
class Dev:
    """Where the log suite writes: the dev stack's URL, and the schema this run owns."""

    dsn: str
    schema: str


@pytest.fixture(scope="session")
def postgres() -> Iterator[Dev]:
    """Probe once. Unreachable is a skip naming the URL, never a silent pass and never a failure."""
    dsn = load_settings().database_url
    shown = _without_password(dsn)
    if reason := _why_it_is_unreachable(dsn):
        pytest.skip(f"postgres is not reachable at {shown}: {reason}")
    # One schema per pytest process, so xdist workers never share a table and the run cleans up
    # after itself with one DROP instead of deleting rows a trigger refuses to delete.
    schema = f"pinecall_test_{os.getpid()}"
    asyncio.run(apply_migrations(dsn, schema=schema))
    try:
        yield Dev(dsn=dsn, schema=schema)
    finally:
        asyncio.run(_drop_schema(dsn, schema))


@pytest.fixture
async def postgres_store(postgres: Dev) -> AsyncIterator[PostgresStore]:
    """A store on this run's schema, with the suite's clock. Its pool is closed either way."""
    store = await PostgresStore.connect(postgres.dsn, schema=postgres.schema, clock=a_clock())
    try:
        yield store
    finally:
        await store.aclose()


@pytest.fixture
async def raw_connection(postgres: Dev) -> AsyncIterator[Any]:
    """A plain connection on this run's schema, for the questions no Store verb asks."""
    connection = await _connect(postgres.dsn, timeout=PROBE_TIMEOUT_SECONDS)
    await connection.execute(f"set search_path to {search_path_of(postgres.schema)}")
    try:
        yield connection
    finally:
        await connection.close()


@pytest.fixture
def agent() -> str:
    """A slug nobody else in this schema uses: list_calls asks by agent, and rows outlive a test."""
    return f"agent-{uuid4().hex[:12]}"


@pytest.fixture
def call() -> str:
    """A call id nobody else in this schema uses. Never assume the table starts empty."""
    return f"CA_{uuid4().hex[:12]}"


@pytest.fixture
def memory_store() -> Store:
    """The in-memory twin, on the same clock."""
    return MemoryStore(clock=a_clock())


def _why_it_is_unreachable(dsn: str) -> str | None:
    """None when it answered. Otherwise the reason, as a person would read it in a terminal."""
    try:
        asyncio.run(_ask_it_for_one(dsn))
    except Exception as failure:  # noqa: BLE001 — whatever stopped it IS the reason
        message = str(failure).strip()
        return f"{type(failure).__name__}: {message}" if message else type(failure).__name__
    return None


async def _ask_it_for_one(dsn: str) -> None:
    """The cheapest question a database can answer."""
    connection = await _connect(dsn, timeout=PROBE_TIMEOUT_SECONDS)
    try:
        await connection.execute("select 1")
    finally:
        await connection.close()


async def _drop_schema(dsn: str, schema: str) -> None:
    """The whole copy goes at once: DROP is DDL, and the append-only triggers do not speak to it."""
    connection = await _connect(dsn, timeout=PROBE_TIMEOUT_SECONDS)
    try:
        await connection.execute(f"drop schema if exists {schema} cascade")
    finally:
        await connection.close()


def _without_password(dsn: str) -> str:
    """A skip reason is read out loud and pasted into issues; the password never travels with it."""
    parts = urlsplit(dsn)
    if parts.password is None:
        return dsn
    host = parts.hostname or ""
    host = f"[{host}]" if ":" in host else host
    if parts.port is not None:
        host = f"{host}:{parts.port}"
    netloc = f"{parts.username}@{host}" if parts.username else host
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
