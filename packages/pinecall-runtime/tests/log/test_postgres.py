"""What only the Postgres store does: it forgets ephemerals, refuses a rewrite, and keeps a seq."""

import json

import asyncpg  # type: ignore[import-untyped]  # pyright: ignore[reportMissingTypeStubs]
import pytest

from pinecall.db import apply_migrations
from pinecall.log.reduce import reduce
from pinecall.log.store import PostgresStore
from pinecall.log.store.postgres import PostgresStore as Store
from pinecall_protocol import decode_entries, encode
from pinecall_protocol.fixtures import GOLDEN_LOG, GOLDEN_STATE
from tests.support.postgres import Dev

pytestmark = pytest.mark.postgres


async def test_an_ephemeral_leaves_a_hole_where_its_seq_was(
    postgres_store: Store, agent: str, call: str
) -> None:
    """The contract in one test: a seq is spent, no row is written, and since() shows the hole."""
    await postgres_store.append(call, agent, "call.started", {})
    await postgres_store.append(call, agent, "user.transcript", {"text": "ho"}, ephemeral=True)
    await postgres_store.append(call, agent, "user.transcript", {"text": "hol"}, ephemeral=True)
    await postgres_store.append(call, agent, "turn.user", {"text": "hola"})
    assert [entry.seq for entry in await postgres_store.since(call)] == [1, 4]
    # The seq space is whole even though the log is not: latest_seq counts what was handed out,
    # so a reader's cursor at 4 means the same thing to the store as to the reader.
    assert await postgres_store.latest_seq(call) == 4


async def test_the_table_refuses_an_update_and_a_delete(
    postgres_store: Store, raw_connection: object, agent: str, call: str
) -> None:
    """A reader that replayed seq 1 yesterday reads the same entry today, or it is not a log."""
    connection = raw_connection
    await postgres_store.append(call, agent, "call.started", {})
    with pytest.raises(asyncpg.PostgresError, match="append-only"):
        await connection.execute("update call_log set type = 'nope'")  # type: ignore[attr-defined]
    with pytest.raises(asyncpg.PostgresError, match="append-only"):
        await connection.execute("delete from call_log")  # type: ignore[attr-defined]
    assert [entry.type for entry in await postgres_store.since(call)] == ["call.started"]


async def test_migrations_applied_twice_apply_nothing(postgres: Dev) -> None:
    """The record is the guard: `migrate` is safe to run on every deploy, which is the point."""
    assert (await apply_migrations(postgres.dsn, schema=postgres.schema)).applied == ()


async def test_the_golden_log_replays_to_the_same_state_through_postgres(
    postgres: Dev, call: str
) -> None:
    """Acceptance 1: the 124 golden entries in, and what comes back reduces to the same state.

    Two things about the comparison, both deliberate. It is against the durable entries only,
    because the store's contract lets it forget an ephemeral and this one does. And the expected
    side is renumbered by position, because the golden is a *reader's stream*, not a store's
    table: it declares a gap at 39-40, so its seq 39 is an entry the reader never saw, and its
    last marker repeats seq 124. A store numbers what it is given, one after another, so the log
    it hands back is contiguous. Everything else must survive untouched — order, ts, and every
    byte of the jsonb — and the reducer is what proves it, field by field.
    """
    golden = decode_entries(GOLDEN_LOG.read_text(encoding="utf-8"))
    # A fresh call id: the schema outlives the test, and a second replay into one log would
    # number itself 125 onward and reduce to the golden state twice over.
    as_written = [
        entry.model_copy(update={"seq": seq, "call": call}) for seq, entry in enumerate(golden, 1)
    ]
    durable = [entry for entry in as_written if not entry.ephemeral]
    # The store stamps ts from its own clock, so the clock hands back the golden's own timestamps:
    # participant.joined reads joined_at off the entry, and a wall clock would move it.
    ticks = iter([entry.ts for entry in golden])
    store = await PostgresStore.connect(
        postgres.dsn, schema=postgres.schema, clock=lambda: next(ticks)
    )
    try:
        for entry in golden:
            await store.append(call, entry.agent, entry.type, entry.data, entry.ephemeral)
        assert await store.latest_seq(call) == len(golden)
        written = await store.since(call, limit=1000)
    finally:
        await store.aclose()
    assert [encode(entry) for entry in written] == [encode(entry) for entry in durable]
    assert encode(reduce(written)) == encode(reduce(durable))


async def test_the_golden_state_survives_the_round_trip(postgres: Dev, call: str) -> None:
    """The parts of the state no ephemeral touches come back as the golden state file has them."""
    golden = decode_entries(GOLDEN_LOG.read_text(encoding="utf-8"))
    expected = json.loads(GOLDEN_STATE.read_text(encoding="utf-8"))
    ticks = iter([entry.ts for entry in golden])
    store = await PostgresStore.connect(
        postgres.dsn, schema=postgres.schema, clock=lambda: next(ticks)
    )
    try:
        for entry in golden:
            await store.append(call, entry.agent, entry.type, entry.data, entry.ephemeral)
        state = encode(reduce(await store.since(call, limit=1000)))
    finally:
        await store.aclose()
    assert state["app_state"] == expected["app_state"]
    assert state["turns"] == expected["turns"]
    assert state["usage"] == expected["usage"] and state["cost"] == expected["cost"]
