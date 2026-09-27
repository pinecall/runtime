"""What the suites share: a schema per test, a pool, a store, and a vendor nobody ships."""

import logging
import os
import sys
from collections.abc import AsyncIterator, Callable, Iterator
from uuid import uuid4

import pytest
from psycopg import sql

from pinecall.log.store import Store
from pinecall.postgres.migrate import apply_migrations
from pinecall.postgres.pool import Pool, connect, open_pool
from pinecall.providers.build import MODALITIES, Vendor, installed
from tests.fakes import ACME, acme_plugin

DSN = os.environ.get("DATABASE_URL", "")

postgres = pytest.mark.skipif(not DSN, reason="DATABASE_URL: a Postgres, `make test`")

# Tests read the clock, so it is one they can predict: 1.0, then half a second more each time.
FIRST_TICK = 1.0
A_TICK_S = 0.5


@pytest.fixture
async def schema() -> AsyncIterator[str]:
    """A schema of this test alone, with the whole schema applied, dropped at the end."""
    name = f"pinecall_test_{uuid4().hex[:12]}"
    await apply_migrations(DSN, schema=name)
    yield name
    async with await connect(DSN) as connection:
        await connection.execute(
            sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(name))
        )


@pytest.fixture
async def pool(schema: str) -> AsyncIterator[Pool]:
    """A pool on the test's schema."""
    opened = await open_pool(DSN, schema=schema, max_size=4)
    yield opened
    await opened.close()


@pytest.fixture
def ticking() -> Callable[[], float]:
    """A clock that moves half a second per reading, from 1.0."""
    ticks = iter(range(10_000))
    return lambda: FIRST_TICK + A_TICK_S * next(ticks)


@pytest.fixture
def store(pool: Pool, ticking: Callable[[], float]) -> Store:
    """The store on the test's schema, stamping entries with the ticking clock."""
    return Store(pool, clock=ticking)


@pytest.fixture
def call() -> str:
    """A call id nobody else uses."""
    return f"CA_{uuid4().hex[:12]}"


@pytest.fixture
def acme(monkeypatch: pytest.MonkeyPatch) -> str:
    """The name of a vendor installed for this test alone."""
    monkeypatch.setitem(sys.modules, f"livekit.plugins.{ACME}", acme_plugin())
    monkeypatch.setitem(installed(), ACME, Vendor(ACME, frozenset(MODALITIES)))
    return ACME


# livekit's emitter logs a listener's exception and goes on, and asyncio does the same for a
# callback: a listener of ours that breaks would pass every test while the call loses its entries.
@pytest.fixture(autouse=True)
def no_listener_fails_in_silence(caplog: pytest.LogCaptureFixture) -> Iterator[None]:
    """Fail the test when livekit or asyncio swallowed an exception of one of our listeners."""
    caplog.set_level(logging.ERROR, logger="livekit")
    caplog.set_level(logging.ERROR, logger="asyncio")
    yield
    swallowed = [
        record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.ERROR and record.name in {"livekit", "asyncio"}
    ]
    assert swallowed == []
