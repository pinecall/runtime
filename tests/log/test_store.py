"""Tests for the store, on a real Postgres: seqs, sealing, paging, owners, across."""

import asyncio
import json
import signal
import sys
import textwrap

import psycopg
import pytest
from psycopg import sql

from pinecall.domain.agent import Versions
from pinecall.domain.errors import Conflict, DeclarationRefused
from pinecall.domain.scope import Scope
from pinecall.log.reduce import reduce
from pinecall.log.store import (
    AGENT_LOG_PREFIX,
    DEFAULT_LIMIT,
    Claim,
    Claimant,
    Store,
    Unnumbered,
    entry_of,
    log_name,
)
from pinecall.postgres.pool import Pool, connect
from pinecall.wire.frames import Entry
from tests.conftest import DSN, postgres
from tests.wire.golden import GOLDEN_STATE, golden_entries

pytestmark = postgres

AGENT = "dental-sur"


def test_an_agents_log_is_named_after_it_and_a_calls_after_the_call() -> None:
    assert log_name(None, AGENT) == f"{AGENT_LOG_PREFIX}{AGENT}"
    assert log_name("CA_1", AGENT) == "CA_1"


def test_a_row_is_the_wires_envelope() -> None:
    row: dict[str, object] = {
        "call": "CA_1",
        "seq": 3,
        "ts": 2.5,
        "agent": AGENT,
        "type": "custom",
        "ephemeral": False,
        "data": {"name": "n", "data": {}},
        "position": 99,
    }
    assert entry_of(row) == Entry(
        seq=3,
        ts=2.5,
        call="CA_1",
        agent=AGENT,
        type="custom",
        ephemeral=False,
        data={"name": "n", "data": {}},
    )


# ── seqs ──


async def test_the_first_entry_is_seq_one_and_each_append_hands_back_the_next(
    store: Store, call: str
) -> None:
    ringing = await store.append(call, AGENT, "call.ringing", {"from": "+34600"}, ephemeral=False)
    started = await store.append(call, AGENT, "call.started", {}, ephemeral=False)
    assert (ringing.seq, started.seq) == (1, 2)
    assert await store.latest_seq(call) == 2


async def test_seqs_stay_contiguous_under_concurrent_appends(store: Store, call: str) -> None:
    entries = await asyncio.gather(
        *(store.append(call, AGENT, "custom", {"n": n}, ephemeral=False) for n in range(50))
    )
    assert sorted(entry.seq for entry in entries) == list(range(1, 51))
    assert [entry.seq for entry in await store.since(call)] == list(range(1, 51))


async def test_an_ephemeral_takes_a_seq_and_leaves_a_hole_where_its_row_would_be(
    store: Store, call: str
) -> None:
    await store.append(call, AGENT, "call.started", {}, ephemeral=False)
    interim = await store.append(call, AGENT, "user.transcript", {"text": "ho"}, ephemeral=True)
    await store.append(call, AGENT, "user.transcript", {"text": "hol"}, ephemeral=True)
    final = await store.append(call, AGENT, "turn.user", {"text": "hola"}, ephemeral=False)
    assert (interim.seq, interim.ephemeral, final.seq, final.ephemeral) == (2, True, 4, False)
    assert [entry.seq for entry in await store.since(call)] == [1, 4]
    assert await store.latest_seq(call) == 4


async def test_a_call_nobody_wrote_to_has_seq_zero_and_is_not_sealed(
    store: Store, call: str
) -> None:
    assert await store.latest_seq(call) == 0
    assert not await store.sealed(call)
    assert await store.since(call) == []


async def test_the_store_hands_back_the_wires_envelope_stamped_by_its_clock(
    store: Store, call: str
) -> None:
    entry = await store.append(call, AGENT, "call.started", {"channel": "phone"}, ephemeral=False)
    assert entry.written() == {
        "seq": 1,
        "ts": 1.0,
        "call": call,
        "agent": AGENT,
        "type": "call.started",
        "ephemeral": False,
        "data": {"channel": "phone"},
    }
    assert await store.since(call) == [entry]


# ── a worker's batches ──


def batch_of(*types: str) -> list[Unnumbered]:
    return [
        Unnumbered(type=kind, data={"n": n}, ephemeral=kind == "user.transcript", ts=0.5)
        for n, kind in enumerate(types)
    ]


HEAD_COUNTS = "select seq, written, written_seq from call_log_head where log = %(log)s"

STARTED_AT = "select started_at from call_log_head where log = %(log)s"


async def head_counts(store: Store, call: str) -> tuple[int, int, int | None]:
    async with store.pool.connection() as connection:
        row = await (await connection.execute(HEAD_COUNTS, {"log": call})).fetchone()
    assert row is not None
    return int(row["seq"]), int(row["written"]), row["written_seq"]


async def test_a_batch_takes_contiguous_seqs_and_its_rows_are_there(
    store: Store, call: str
) -> None:
    batch = await store.append_many(call, AGENT, batch_of("custom", "custom", "custom"), after=0)
    assert (batch.replayed, [entry.seq for entry in batch.entries]) == (False, [1, 2, 3])
    assert [(entry.seq, entry.data) for entry in await store.since(call)] == [
        (1, {"n": 0}),
        (2, {"n": 1}),
        (3, {"n": 2}),
    ]
    assert await head_counts(store, call) == (3, 3, 3)


async def test_the_same_batch_again_answers_the_same_seqs_and_writes_nothing(
    store: Store, call: str
) -> None:
    first = await store.append_many(call, AGENT, batch_of("custom", "turn.user"), after=0)
    again = await store.append_many(call, AGENT, batch_of("custom", "turn.user"), after=0)
    assert again.replayed
    assert [entry.written() for entry in again.entries] == [
        entry.written() for entry in first.entries
    ]
    assert [entry.seq for entry in await store.since(call)] == [1, 2]
    assert await head_counts(store, call) == (2, 2, 2)


async def test_a_batch_after_the_gateways_own_entry_counts_only_the_workers(
    store: Store, call: str
) -> None:
    await store.append(call, AGENT, "call.ringing", {}, ephemeral=False)
    first = await store.append_many(call, AGENT, batch_of("custom", "custom"), after=0)
    await store.append(call, AGENT, "tool.call", {}, ephemeral=False)
    second = await store.append_many(call, AGENT, batch_of("custom"), after=2)
    assert [entry.seq for entry in [*first.entries, *second.entries]] == [2, 3, 5]
    assert await head_counts(store, call) == (5, 3, 5)
    replayed = await store.append_many(call, AGENT, batch_of("custom"), after=2)
    assert [entry.seq for entry in replayed.entries] == [5]
    assert [entry.seq for entry in await store.since(call)] == [1, 2, 3, 4, 5]


async def test_the_count_a_writer_resumes_from_is_the_heads_and_zero_for_a_new_log(
    store: Store, call: str
) -> None:
    assert await store.written(call) == 0
    await store.append(call, AGENT, "call.ringing", {}, ephemeral=False)
    await store.append_many(call, AGENT, batch_of("custom", "user.transcript"), after=0)
    await store.append(call, AGENT, "tool.call", {}, ephemeral=False)
    assert await store.written(call) == 2


async def test_each_entry_keeps_its_own_time_bounded_by_the_clock_and_never_stepping_back(
    pool: Pool, call: str
) -> None:
    store = Store(pool, clock=lambda: 10.0)
    times = [3.0, 2.0, 4.0, 12.0, 5.0]
    batch = [
        Unnumbered(type="custom", data={"n": n}, ephemeral=False, ts=ts)
        for n, ts in enumerate(times)
    ]
    written = await store.append_many(call, AGENT, batch, after=0)
    assert [entry.ts for entry in written.entries] == [3.0, 3.0, 4.0, 10.0, 10.0]
    assert [entry.ts for entry in await store.since(call)] == [3.0, 3.0, 4.0, 10.0, 10.0]
    again = await store.append_many(call, AGENT, batch, after=0)
    assert [entry.written() for entry in again.entries] == [
        entry.written() for entry in written.entries
    ]
    async with pool.connection() as connection:
        row = await (await connection.execute(STARTED_AT, {"log": call})).fetchone()
    assert row is not None
    assert row["started_at"] == 3.0


async def test_a_batch_sent_twice_at_once_is_written_once_and_both_hear_the_same_seqs(
    store: Store, call: str
) -> None:
    await store.append(call, AGENT, "call.ringing", {}, ephemeral=False)
    batch = batch_of("custom", "user.transcript", "custom")
    first, second = await asyncio.gather(
        store.append_many(call, AGENT, batch, after=0),
        store.append_many(call, AGENT, batch, after=0),
    )
    assert sorted([first.replayed, second.replayed]) == [False, True]
    assert [entry.seq for entry in first.entries] == [entry.seq for entry in second.entries]
    assert [entry.seq for entry in first.entries] == [2, 3, 4]
    assert [entry.seq for entry in await store.since(call)] == [1, 2, 4]
    assert await head_counts(store, call) == (4, 3, 4)


async def test_a_first_batch_that_says_something_came_before_it_is_refused(
    store: Store, call: str
) -> None:
    with pytest.raises(Conflict, match="took 0 entries from its worker"):
        await store.append_many(call, AGENT, batch_of("custom"), after=2)
    assert await store.latest_seq(call) == 0


@pytest.mark.parametrize(("after", "size"), [(3, 1), (0, 1), (1, 2), (0, 3)])
async def test_a_batch_that_is_neither_next_nor_the_last_again_is_refused_and_writes_nothing(
    store: Store, call: str, after: int, size: int
) -> None:
    await store.append_many(call, AGENT, batch_of("custom", "custom"), after=0)
    with pytest.raises(Conflict, match="took 2 entries from its worker"):
        await store.append_many(call, AGENT, batch_of(*["custom"] * size), after=after)
    assert await head_counts(store, call) == (2, 2, 2)
    assert [entry.seq for entry in await store.since(call)] == [1, 2]


async def test_a_sealed_log_refuses_a_new_batch(store: Store, call: str) -> None:
    await store.append_many(call, AGENT, batch_of("custom"), after=0)
    await store.seal(call)
    with pytest.raises(Conflict, match="has ended"):
        await store.append_many(call, AGENT, batch_of("custom"), after=1)
    assert await head_counts(store, call) == (1, 1, 1)


async def test_an_ephemeral_in_a_batch_takes_its_seq_and_no_row(store: Store, call: str) -> None:
    batch = await store.append_many(
        call, AGENT, batch_of("user.transcript", "turn.user", "user.transcript"), after=0
    )
    assert [(entry.seq, entry.ephemeral) for entry in batch.entries] == [
        (1, True),
        (2, False),
        (3, True),
    ]
    assert [entry.seq for entry in await store.since(call)] == [2]
    assert await store.latest_seq(call) == 3


@pytest.mark.parametrize("size", [0, 257])
async def test_an_empty_batch_and_one_past_the_most_are_refused(
    store: Store, call: str, size: int
) -> None:
    with pytest.raises(DeclarationRefused, match="1 to 256"):
        await store.append_many(call, AGENT, batch_of(*["custom"] * size), after=0)
    assert await store.latest_seq(call) == 0


# ── sealing ──


async def test_a_sealed_log_refuses_every_later_append_and_says_it_is_sealed(
    store: Store, call: str
) -> None:
    await store.append(call, AGENT, "call.started", {}, ephemeral=False)
    await store.seal(call)
    with pytest.raises(Conflict, match="has ended"):
        await store.append(call, AGENT, "turn.user", {"text": "too late"}, ephemeral=False)
    await store.seal(call)
    assert await store.sealed(call)
    assert await store.latest_seq(call) == 1


async def test_a_log_can_be_sealed_before_anything_was_written_to_it(
    store: Store, call: str
) -> None:
    await store.seal(call)
    assert await store.sealed(call)
    with pytest.raises(Conflict, match="has ended"):
        await store.append(call, AGENT, "call.ringing", {}, ephemeral=False)


async def test_a_verdict_is_the_one_entry_a_sealed_log_still_takes(store: Store, call: str) -> None:
    await store.append(call, AGENT, "call.summary", {"x": 1}, ephemeral=False)
    await store.seal(call)
    score = await store.rescored(call, AGENT, {"passed": True, "judges": [], "judge_calls": 1})
    assert (score.seq, score.type, score.ephemeral) == (2, "call.score", False)
    assert [entry.type for entry in await store.since(call)] == ["call.summary", "call.score"]
    with pytest.raises(Conflict, match="has no log to judge"):
        await store.rescored(f"{call}-nobody", AGENT, {})


# ── paging ──


async def test_since_never_returns_a_seq_at_or_below_the_cursor(store: Store, call: str) -> None:
    for n in range(5):
        await store.append(call, AGENT, "custom", {"n": n}, ephemeral=False)
    for after in range(7):
        assert all(entry.seq > after for entry in await store.since(call, after=after))
    assert [entry.seq for entry in await store.since(call, after=3)] == [4, 5]


async def test_since_pages_in_seq_order_and_stops_at_the_limit(store: Store, call: str) -> None:
    for n in range(5):
        await store.append(call, AGENT, "custom", {"n": n}, ephemeral=False)
    assert [entry.seq for entry in await store.since(call, after=1, limit=2)] == [2, 3]


async def test_whole_reads_every_page_and_starts_from_the_cursor(store: Store, call: str) -> None:
    many = DEFAULT_LIMIT + 3
    await asyncio.gather(
        *(store.append(call, AGENT, "custom", {"n": n}, ephemeral=False) for n in range(many))
    )
    assert [entry.seq for entry in await store.whole(call)] == list(range(1, many + 1))
    assert [entry.seq for entry in await store.whole(call, after=many - 2)] == [many - 1, many]
    assert await store.whole(f"{call}-nobody") == []


# ── the agent's own log ──


async def test_the_agents_own_log_has_a_seq_of_its_own(store: Store, call: str) -> None:
    registered = await store.append(
        None, AGENT, "agent.registered", {"routes": []}, ephemeral=False
    )
    ringing = await store.append(call, AGENT, "call.ringing", {}, ephemeral=False)
    configured = await store.append(None, AGENT, "agent.configured", {}, ephemeral=False)
    assert (registered.seq, ringing.seq, configured.seq) == (1, 1, 2)
    assert (registered.call, ringing.call) == (None, call)
    own = await store.since(log_name(None, AGENT))
    assert [entry.type for entry in own] == ["agent.registered", "agent.configured"]
    assert [entry.seq for entry in await store.since(log_name(None, AGENT), after=1)] == [2]


# ── owners ──


async def test_a_log_is_the_first_orgs_that_claims_it_and_never_moves(
    store: Store, call: str
) -> None:
    assert await store.claimant(call, AGENT) is None
    await store.claim(call, AGENT, "clinica")
    await store.claim(call, AGENT, "tienda")
    await store.append(call, AGENT, "call.started", {}, ephemeral=False)
    assert await store.claimant(call, AGENT) == Claimant("clinica", None)


async def test_a_claim_may_come_before_the_first_entry_and_carries_the_corner(
    store: Store, call: str, pool: Pool
) -> None:
    claim = Claim(Scope("clinica", "sandbox", "m_berna"), Versions(config=3, lexicon=1))
    await store.claim(call, AGENT, "clinica", claim)
    await store.claim(call, AGENT, "clinica", Claim(Scope("clinica"), Versions(config=9)))
    async with pool.connection() as connection:
        row = await (
            await connection.execute(
                "select env, holder, config_version, lexicon_version"
                " from call_log_head where log = %s",
                (call,),
            )
        ).fetchone()
    assert row == {"env": "sandbox", "holder": "m_berna", "config_version": 3, "lexicon_version": 1}


async def test_the_agents_own_log_has_an_owner_of_its_own(store: Store, call: str) -> None:
    await store.claim(None, AGENT, "clinica")
    assert await store.owner(AGENT) == "clinica"
    assert await store.claimant(call, AGENT) is None


async def test_a_logs_claimant_is_its_org_and_world_and_an_agents_log_has_no_world(
    store: Store, call: str
) -> None:
    assert await store.claimant(call, AGENT) is None
    await store.claim(call, AGENT, "clinica", Claim(Scope("clinica", "sandbox")))
    await store.claim(None, AGENT, "clinica")
    assert await store.claimant(call, AGENT) == Claimant("clinica", "sandbox")
    assert await store.claimant(None, AGENT) == Claimant("clinica", None)


async def test_the_operator_moves_every_log_of_an_agent_to_another_org(
    store: Store, call: str
) -> None:
    await store.claim(call, AGENT, "wrong")
    await store.claim(None, AGENT, "wrong")
    assert await store.moved(AGENT, "right") == 2
    assert await store.owner(AGENT) == "right"
    assert await store.claimant(call, AGENT) == Claimant("right", None)
    assert await store.moved(f"{AGENT}-nobody", "right") == 0


# ── across every log ──


async def test_across_pages_the_metered_types_of_every_log_by_position(
    store: Store, call: str
) -> None:
    other = f"{call}-b"
    await store.claim(call, AGENT, "clinica")
    await store.claim(other, AGENT, "tienda")
    await store.append(call, AGENT, "call.started", {}, ephemeral=False)
    first = await store.append(call, AGENT, "call.summary", {"duration_s": 1}, ephemeral=False)
    await store.append(other, AGENT, "user.transcript", {"text": "hola"}, ephemeral=True)
    second = await store.append(other, AGENT, "call.summary", {"duration_s": 2}, ephemeral=False)
    rows = await store.across(("call.summary",))
    assert [(row.org, row.entry.seq) for row in rows] == [
        ("clinica", first.seq),
        ("tienda", second.seq),
    ]
    assert rows[0].position < rows[1].position
    resumed = await store.across(("call.summary",), after=rows[0].position)
    assert [row.entry.call for row in resumed] == [other]
    assert await store.across(("call.summary",), after=rows[1].position) == []


async def test_the_operator_sees_the_newest_calls_and_the_newest_one_still_live(
    store: Store, call: str
) -> None:
    older, newer = f"{call}-a", f"{call}-b"
    await store.append(older, AGENT, "call.ringing", {}, ephemeral=False)
    await store.append(newer, f"{AGENT}-2", "call.ringing", {}, ephemeral=False)
    assert await store.newest_calls(5) == [newer, older]
    assert await store.newest_calls(5, agent=AGENT) == [older]
    assert await store.newest_live_call() == newer
    await store.seal(newer)
    assert await store.newest_live_call() == older


# ── the table itself ──


async def test_the_table_refuses_an_update_and_a_delete(
    store: Store, call: str, schema: str
) -> None:
    await store.append(call, AGENT, "call.started", {}, ephemeral=False)
    async with await connect(DSN) as connection:
        await connection.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema)))
        with pytest.raises(psycopg.Error, match="append-only"):
            await connection.execute("update call_log set type = 'nope'")
        with pytest.raises(psycopg.Error, match="append-only"):
            await connection.execute("delete from call_log")
    assert [entry.type for entry in await store.since(call)] == ["call.started"]


async def test_the_golden_log_replays_to_the_same_state_through_postgres(
    pool: Pool, call: str
) -> None:
    golden = golden_entries()
    ticks = iter(entry.ts for entry in golden)
    store = Store(pool, clock=lambda: next(ticks))
    for entry in golden:
        await store.append(call, entry.agent, entry.type, entry.data, ephemeral=entry.ephemeral)
    written = await store.whole(call)
    # The golden is a reader's stream, with a gap at 39-40, so it is compared renumbered.
    durable = [
        entry.model_copy(update={"seq": seq, "call": call})
        for seq, entry in enumerate(golden, 1)
        if not entry.ephemeral
    ]
    assert await store.latest_seq(call) == len(golden)
    assert [entry.written() for entry in written] == [entry.written() for entry in durable]
    assert reduce(written).written() == reduce(durable).written()
    expected = json.loads(GOLDEN_STATE.read_text(encoding="utf-8"))
    state = reduce(written).written()
    for field in ("app_state", "turns", "usage", "cost"):
        assert state[field] == expected[field]


WRITER = textwrap.dedent(
    """
    import asyncio, sys
    from pinecall.log.store import Store
    from pinecall.postgres.pool import open_pool

    async def main(dsn, schema, call, agent):
        store = Store(await open_pool(dsn, schema=schema, max_size=1))
        while True:
            tick = {"name": "tick", "data": {}}
            entry = await store.append(call, agent, "custom", tick, ephemeral=False)
            sys.stdout.write(f"{entry.seq}\\n")
            sys.stdout.flush()

    asyncio.run(main(*sys.argv[1:5]))
    """
)
APPENDS_BEFORE_THE_KILL = 30


# One statement writes the counter and the row, so a writer killed between them cannot exist.
async def test_a_killed_writer_leaves_a_contiguous_log_and_no_torn_row(
    store: Store, call: str, schema: str
) -> None:
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-c", WRITER, DSN, schema, call, AGENT, stdout=asyncio.subprocess.PIPE
    )
    assert process.stdout is not None
    try:
        last = 0
        for _ in range(APPENDS_BEFORE_THE_KILL):
            last = int(await asyncio.wait_for(process.stdout.readline(), timeout=20))
        # SIGKILL, not terminate: the writer must not get to finish its append.
        process.send_signal(signal.SIGKILL)
        assert await process.wait() == -signal.SIGKILL
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    entries = await store.whole(call)
    assert [entry.seq for entry in entries] == list(range(1, len(entries) + 1)), "a hole"
    assert len(entries) >= last, "an entry the writer was told it had is not there"
    assert await store.latest_seq(call) == len(entries), "an unfinished append moved the counter"
    assert all(entry.data == {"name": "tick", "data": {}} for entry in entries), "a torn row"
