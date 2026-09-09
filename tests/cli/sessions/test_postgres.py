"""list and show against the real database: the golden written through the store, then read back."""

from io import StringIO
from uuid import uuid4

import pytest

from pinecall.cli.sessions import verbs as sessions
from pinecall.cli.sessions.source import Source
from pinecall.log.store import PostgresStore
from pinecall_protocol import decode_entries
from pinecall_protocol.envelope import Entry
from pinecall_protocol.fixtures import GOLDEN_LOG
from tests.cli.conftest import Dev

pytestmark = pytest.mark.postgres


@pytest.fixture
def golden() -> list[Entry]:
    return decode_entries(GOLDEN_LOG.read_text(encoding="utf-8"))


async def load(postgres: Dev, call: str, agent: str, golden: list[Entry]) -> None:
    """The golden into one call's log. The store's clock hands back the fixture's own timestamps."""
    ticks = iter([entry.ts for entry in golden])
    store = await PostgresStore.connect(postgres.dsn, schema=postgres.schema, clock=ticks.__next__)
    try:
        for entry in golden:
            await store.append(call, agent, entry.type, entry.data, entry.ephemeral)
    finally:
        await store.aclose()


async def test_list_and_show_read_a_call_that_was_written_through_the_store(
    postgres: Dev, golden: list[Entry]
) -> None:
    """The path a person runs: entries appended, then `list` and `show` off Postgres itself."""
    agent = f"agent-{uuid4().hex[:12]}"
    call = f"CA_{uuid4().hex[:12]}"
    await load(postgres, call, agent, golden)
    source = await Source.open(postgres.dsn, schema=postgres.schema)
    try:
        listed, shown = StringIO(), StringIO()
        assert await sessions.list_calls(agent, 20, source, listed) == 0
        assert await sessions.show_call(call, source, shown) == 0
    finally:
        await source.aclose()
    assert listed.getvalue().startswith(f"{call}  phone  2026-08-12 ")
    assert listed.getvalue().rstrip().endswith("0.0229 EUR")
    lines = shown.getvalue().splitlines()
    # The ephemerals the store forgot are the only difference from the fixture, so the seqs it
    # hands back are the ones it assigned — and every metric block is still there, field by field.
    assert len(lines) > len(golden)
    assert "· metrics.e2e_latency" in shown.getvalue()
    assert "· tokens_per_second" in shown.getvalue()
    assert lines[-1].startswith("  e2e_latency")


async def test_the_newest_live_call_is_the_one_nothing_sealed(
    postgres: Dev, golden: list[Entry]
) -> None:
    """What `tail` follows with no id given: sealing a log takes it out of the running."""
    agent = f"agent-{uuid4().hex[:12]}"
    ended, running = f"CA_{uuid4().hex[:12]}", f"CA_{uuid4().hex[:12]}"
    await load(postgres, ended, agent, golden[:20])
    await load(postgres, running, agent, golden[:20])
    source = await Source.open(postgres.dsn, schema=postgres.schema)
    try:
        await source.store.seal(ended)
        assert await source.newest_live_call() != ended
        assert running in await source.calls(agent, 20)
    finally:
        await source.aclose()
