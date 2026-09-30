"""Tests for the writer on a real Postgres: groups, their order, a poisoned member, a crash."""

import asyncio
import logging
import signal
import sys
import textwrap

import psycopg
import pytest

from pinecall.domain.errors import Conflict
from pinecall.log._writer import GATHER_S, MOST_IN_A_GROUP, Append, Unnumbered, Writer
from pinecall.log.store import Store
from pinecall.postgres.pool import Pool
from tests.conftest import DSN, postgres

pytestmark = postgres

AGENT = "dental-sur"

# Rows one transaction wrote share its xid: how many transactions wrote the logs.
TRANSACTIONS = "select count(distinct xmin::text) as written from call_log where log = any(%s)"


def an_entry(n: int, *, kind: str = "custom", ephemeral: bool = False) -> Unnumbered:
    return Unnumbered(type=kind, data={"n": n}, ephemeral=ephemeral, ts=float(n))


def a_batch(first: int, n: int) -> list[Unnumbered]:
    return [an_entry(first + place) for place in range(n)]


async def transactions(pool: Pool, logs: list[str]) -> int:
    async with pool.connection() as connection:
        row = await (await connection.execute(TRANSACTIONS, (logs,))).fetchone()
    assert row is not None
    return int(row["written"])


# ── groups ──


async def test_the_appends_of_many_calls_at_once_are_written_in_few_transactions(
    store: Store, call: str
) -> None:
    calls = [f"{call}-{n}" for n in range(40)]
    entries = await asyncio.gather(
        *(store.append(each, AGENT, "custom", {"n": 1}, ephemeral=False) for each in calls)
    )
    assert [entry.seq for entry in entries] == [1] * len(calls)
    assert await transactions(store.pool, calls) < len(calls) / 4


async def test_calls_interleaved_keep_contiguous_seqs_in_the_order_each_asked(
    store: Store, call: str
) -> None:
    calls = [f"{call}-{n}" for n in range(12)]

    async def talking(each: str) -> list[int]:
        seqs: list[int] = []
        after = 0
        for turn in range(6):
            if turn % 2 == 0:
                entry = await store.append(each, AGENT, "custom", {"t": turn}, ephemeral=turn == 4)
                seqs.append(entry.seq)
                continue
            batch = await store.append_many(each, AGENT, a_batch(turn, 3), after=after)
            after += 3
            seqs += [entry.seq for entry in batch.entries]
        return seqs

    heard = await asyncio.gather(*(talking(each) for each in calls))
    for each, seqs in zip(calls, heard, strict=True):
        assert seqs == list(range(1, 13))
        kept = [entry.seq for entry in await store.whole(each)]
        assert kept == [seq for seq in range(1, 13) if seq != 9], "the ephemeral took seq 9"
        assert await store.written(each) == 9


async def test_requests_to_one_log_at_once_take_their_seqs_in_the_order_they_came(
    store: Store, call: str
) -> None:
    requests = [
        asyncio.create_task(store.append(call, AGENT, "custom", {"n": n}, ephemeral=False))
        for n in range(8)
    ]
    entries = await asyncio.gather(*requests)
    assert [(entry.data["n"], entry.seq) for entry in entries] == [(n, n + 1) for n in range(8)]
    assert [entry.data for entry in await store.whole(call)] == [{"n": n} for n in range(8)]


async def test_a_group_carries_at_most_its_size_and_the_rest_go_in_the_next(
    store: Store, call: str
) -> None:
    calls = [f"{call}-{n}" for n in range(4)]
    each_batch = MOST_IN_A_GROUP // 2
    batches = await asyncio.gather(
        *(store.append_many(each, AGENT, a_batch(0, each_batch), after=0) for each in calls)
    )
    assert all(len(batch.entries) == each_batch for batch in batches)
    assert await transactions(store.pool, calls) >= 2


async def test_a_lone_append_waits_no_longer_than_the_gather(store: Store, call: str) -> None:
    loop = asyncio.get_running_loop()
    started = loop.time()
    await store.append(call, AGENT, "custom", {}, ephemeral=False)
    # The gather and one transaction on a machine other suites share.
    assert loop.time() - started < GATHER_S + 1.0


# ── one member refused, the others written ──


async def test_a_poisoned_member_is_refused_alone_and_the_rest_of_its_group_is_written(
    store: Store, call: str, caplog: pytest.LogCaptureFixture
) -> None:
    await store.append(f"{call}-sealed", AGENT, "call.started", {}, ephemeral=False)
    await store.seal(f"{call}-sealed")
    await store.append_many(f"{call}-ahead", AGENT, a_batch(0, 2), after=0)
    healthy = [f"{call}-{n}" for n in range(5)]
    with caplog.at_level(logging.WARNING, logger="pinecall.log._writer"):
        answers = await asyncio.gather(
            *(store.append(each, AGENT, "custom", {}, ephemeral=False) for each in healthy),
            # A call id the table's CHECK refuses: the whole group's statement would fail on it.
            store.append("@not-a-call", AGENT, "custom", {}, ephemeral=False),
            store.append(f"{call}-sealed", AGENT, "turn.user", {}, ephemeral=False),
            store.append_many(f"{call}-ahead", AGENT, a_batch(9, 1), after=5),
            return_exceptions=True,
        )
    written, poisoned, sealed, wrong_after = answers[:5], *answers[5:]
    assert "a group of 8 broke" in caplog.text, "the poison was not in the others' group"
    assert all(not isinstance(answer, BaseException) for answer in written)
    assert isinstance(poisoned, psycopg.errors.CheckViolation)
    assert isinstance(sealed, Conflict)
    assert "has ended" in str(sealed)
    assert isinstance(wrong_after, Conflict)
    assert "the batch says 5" in str(wrong_after)
    for each in healthy:
        assert [entry.seq for entry in await store.whole(each)] == [1]
    assert await store.written(f"{call}-ahead") == 2


# ── replay ──


async def test_a_batch_retried_among_other_calls_is_answered_with_its_seqs_and_written_once(
    store: Store, call: str
) -> None:
    batch = a_batch(0, 3)
    first = await store.append_many(call, AGENT, batch, after=0)
    others = [f"{call}-{n}" for n in range(6)]
    retried = asyncio.create_task(store.append_many(call, AGENT, batch, after=0))
    await asyncio.gather(
        *(store.append(each, AGENT, "custom", {}, ephemeral=False) for each in others)
    )
    again = await retried
    assert again.replayed
    assert [entry.seq for entry in again.entries] == [entry.seq for entry in first.entries]
    assert [entry.seq for entry in await store.whole(call)] == [1, 2, 3]


# ── a request given up on, and the end ──


async def test_a_request_given_up_before_its_group_closes_is_not_written(
    pool: Pool, call: str
) -> None:
    writer = Writer(pool)
    append = Append("entry", call, call, AGENT, [an_entry(1)])
    given_up = asyncio.create_task(writer.written(append))
    await asyncio.sleep(0)
    given_up.cancel()
    kept = await writer.written(
        Append("entry", f"{call}-kept", f"{call}-kept", AGENT, [an_entry(1)])
    )
    await writer.drained()
    assert given_up.cancelled()
    assert await Store(pool).latest_seq(call) == 0
    assert [entry.seq for entry in kept.entries] == [1]


async def test_drained_returns_once_every_queued_request_is_written(pool: Pool, call: str) -> None:
    writer = Writer(pool)
    queued = [
        asyncio.create_task(
            writer.written(Append("entry", f"{call}-{n}", f"{call}-{n}", AGENT, [an_entry(n)]))
        )
        for n in range(20)
    ]
    await asyncio.sleep(0)
    await writer.drained()
    assert all(task.done() for task in queued)
    assert [task.result().entries[0].seq for task in queued] == [1] * 20


WRITER = textwrap.dedent(
    """
    import asyncio, sys
    from pinecall.log.store import Store
    from pinecall.postgres.pool import open_pool

    async def talking(store, call, agent):
        while True:
            entry = await store.append(call, agent, "custom", {"n": 1}, ephemeral=False)
            sys.stdout.write(f"{call} {entry.seq}\\n")
            sys.stdout.flush()

    async def main(dsn, schema, call, agent):
        store = Store(await open_pool(dsn, schema=schema, max_size=2))
        await asyncio.gather(*(talking(store, f"{call}-{n}", agent) for n in range(8)))

    asyncio.run(main(*sys.argv[1:5]))
    """
)
ANSWERS_BEFORE_THE_KILL = 200


# Killed with groups gathered and in flight: every seq a request was answered with is stored.
async def test_a_writer_killed_mid_group_loses_nothing_it_answered(
    store: Store, call: str, schema: str
) -> None:
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-c", WRITER, DSN, schema, call, AGENT, stdout=asyncio.subprocess.PIPE
    )
    assert process.stdout is not None
    answered: dict[str, int] = {}
    try:
        for _ in range(ANSWERS_BEFORE_THE_KILL):
            line = await asyncio.wait_for(process.stdout.readline(), timeout=20)
            each, seq = line.decode().split()
            answered[each] = max(answered.get(each, 0), int(seq))
        process.send_signal(signal.SIGKILL)
        assert await process.wait() == -signal.SIGKILL
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    for each, last in answered.items():
        entries = await store.whole(each)
        assert [entry.seq for entry in entries] == list(range(1, len(entries) + 1)), "a hole"
        assert len(entries) >= last, f"{each} was answered seq {last} and has {len(entries)}"
        assert await store.latest_seq(each) == len(entries), "an unwritten group moved a head"
