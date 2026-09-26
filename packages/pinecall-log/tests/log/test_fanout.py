"""The fanout's one promise: an append never waits on a reader, whatever the reader is doing."""

import time

import pytest

from pinecall.log.entry import Entry
from pinecall.log.fanout import QUEUE_DEPTH, Fanout
from pinecall.log.filters import Filter

pytestmark = pytest.mark.unit

AGENT = "clinica-norte"
CALL = "CA_1"

# Enough publishes to fill a 256-deep queue many times over. A fanout that waited on a reader
# would take as long as the reader; one that drops takes microseconds.
A_BURST = 5_000

# Generous by three orders of magnitude: this is a wall against blocking, not a benchmark.
THE_BURST_MUST_TAKE_UNDER_SECONDS = 1.0


def an_entry(seq: int, type: str = "custom", ephemeral: bool = False) -> Entry:
    """One entry, numbered, of whatever type the test is about."""
    return Entry(
        seq=seq, ts=float(seq), call=CALL, agent=AGENT, type=type, ephemeral=ephemeral, data={}
    )


async def test_every_reader_gets_every_entry_in_the_order_it_was_written() -> None:
    fanout = Fanout()
    first, second = fanout.subscribe(), fanout.subscribe()
    for seq in range(1, 4):
        fanout.publish(an_entry(seq))
    fanout.close()
    assert [entry.seq async for entry in first] == [1, 2, 3]
    assert [entry.seq async for entry in second] == [1, 2, 3]


async def test_a_readers_filter_narrows_what_reaches_its_queue() -> None:
    fanout = Fanout()
    subscription = fanout.subscribe(Filter.of("turn.user"))
    fanout.publish(an_entry(1, "turn.user"))
    fanout.publish(an_entry(2, "metrics.llm"))
    fanout.publish(an_entry(3, "call.ended"))
    fanout.close()
    # call.ended is in the always-pass set: a filter narrows what a reader sees, never whether it
    # learns the call is over.
    assert [entry.type async for entry in subscription] == ["turn.user", "call.ended"]


async def test_a_slow_reader_is_dropped_and_the_burst_never_waits_for_it() -> None:
    """Acceptance 3, timed: nobody reads, and 5000 publishes still take under a second."""
    fanout = Fanout()
    slow = fanout.subscribe()
    started = time.perf_counter()
    for seq in range(1, A_BURST + 1):
        fanout.publish(an_entry(seq))
    elapsed = time.perf_counter() - started
    assert elapsed < THE_BURST_MUST_TAKE_UNDER_SECONDS, f"publish waited: {elapsed:.3f}s"
    assert slow.dropped and fanout.readers == 0


async def test_a_dropped_reader_still_reads_what_it_was_given_and_then_ends() -> None:
    """What it has is whole and in order; what follows the drop it missed, and the stream ends."""
    fanout = Fanout()
    slow = fanout.subscribe()
    for seq in range(1, QUEUE_DEPTH + 51):
        fanout.publish(an_entry(seq))
    read = [entry.seq async for entry in slow]
    assert read == list(range(1, QUEUE_DEPTH + 1))
    assert slow.dropped


async def test_a_fast_reader_keeps_up_while_a_slow_one_is_dropped() -> None:
    fanout = Fanout()
    slow, fast = fanout.subscribe(), fanout.subscribe()
    for seq in range(1, QUEUE_DEPTH + 2):
        fanout.publish(an_entry(seq))
        assert (await anext(fast)).seq == seq
    assert slow.dropped and not fast.dropped
    assert fanout.readers == 1


async def test_closing_the_log_ends_every_iteration_and_a_late_reader_gets_nothing() -> None:
    fanout = Fanout()
    early = fanout.subscribe()
    fanout.publish(an_entry(1))
    fanout.close()
    assert [entry.seq async for entry in early] == [1]
    assert [entry.seq async for entry in fanout.subscribe()] == []
