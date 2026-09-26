"""Reading a log while it is written: the backlog, the marker, the live half, and the gap."""

import pytest

from pinecall.log import replay
from pinecall.log.fanout import QUEUE_DEPTH, Fanout
from pinecall.log.filters import Filter
from pinecall.log.logs import CallLog
from pinecall.log.store import MemoryStore, Store

pytestmark = pytest.mark.unit

AGENT = "clinica-norte"
CALL = "CA_1"


def a_log(store: Store) -> CallLog:
    """A call log on the store under test, with a fanout of its own."""
    return CallLog(store, AGENT, CALL, fanout=Fanout())


def a_note(n: int) -> dict[str, object]:
    """A `custom` payload: the one event shape a test can invent without inventing a call."""
    return {"name": "note", "data": {"n": n}}


async def test_the_backlog_comes_first_then_the_marker_then_the_live_half(
    memory_store: MemoryStore,
) -> None:
    log = a_log(memory_store)
    await log.append("custom", a_note(1))
    await log.append("custom", a_note(2))
    stream = log.stream()
    assert [(await anext(stream)).seq for _ in range(2)] == [1, 2]
    marker = await anext(stream)
    assert (marker.type, marker.seq) == ("log.caught_up", 2)
    await log.append("custom", a_note(3))
    assert (await anext(stream)).seq == 3


async def test_an_empty_log_says_caught_up_at_zero(memory_store: MemoryStore) -> None:
    """A reader of a log nobody has written to is already live; it should be told so at once."""
    marker = await anext(a_log(memory_store).stream())
    assert (marker.type, marker.seq, marker.ephemeral) == ("log.caught_up", 0, True)


async def test_the_cursor_is_the_whole_protocol(memory_store: MemoryStore) -> None:
    log = a_log(memory_store)
    for n in range(1, 6):
        await log.append("custom", a_note(n))
    stream = log.stream(after=3)
    assert [(await anext(stream)).seq for _ in range(2)] == [4, 5]
    assert (await anext(stream)).type == "log.caught_up"


async def test_the_backlog_pages_and_the_reader_never_sees_the_pages(
    memory_store: MemoryStore,
) -> None:
    """A page is the store's business. What the reader gets is one stream, in order, whole."""
    log = a_log(memory_store)
    for n in range(1, 8):
        await log.append("custom", a_note(n))
    stream = replay.stream(memory_store, Fanout(), CALL, limit=2)
    assert [(await anext(stream)).seq for _ in range(7)] == list(range(1, 8))
    assert (await anext(stream)).seq == 7


async def test_a_filter_narrows_the_backlog_and_the_live_half_alike(
    memory_store: MemoryStore,
) -> None:
    log = a_log(memory_store)
    await log.append("custom", a_note(1))
    await log.append("state.changed", {"state": {}, "changed": []})
    stream = log.stream(filter=Filter.of("custom"))
    assert (await anext(stream)).type == "custom"
    assert (await anext(stream)).type == "log.caught_up"
    await log.append("state.changed", {"state": {}, "changed": []})
    await log.append("custom", a_note(4))
    assert (await anext(stream)).seq == 4


async def test_a_reader_that_falls_behind_gets_a_gap_with_the_state_in_it(
    memory_store: MemoryStore,
) -> None:
    """The lie the references told was a truncated list. This says gap, and says it with a state."""
    log = a_log(memory_store)
    await log.append("custom", a_note(1))
    stream = log.stream()
    assert (await anext(stream)).seq == 1
    assert (await anext(stream)).type == "log.caught_up"
    # Nobody is reading while the call keeps talking: the queue fills and the reader is dropped.
    behind = QUEUE_DEPTH + 44
    for n in range(2, 2 + behind):
        await log.append("custom", a_note(n))
    given = [(await anext(stream)).seq for _ in range(QUEUE_DEPTH)]
    assert given == list(range(2, 2 + QUEUE_DEPTH)), "what it was given is whole and in order"

    gap = await anext(stream)
    last = 1 + behind
    assert gap.type == "log.gap" and gap.ephemeral
    assert gap.seq == last, "a marker stands at the seq of the last entry it speaks for"
    assert gap.data["from_seq"] == 2 + QUEUE_DEPTH and gap.data["to_seq"] == last
    # One entry catches the reader up, instead of the four hundred it missed.
    assert gap.data["snapshot"]["seq"] == last
    assert len(gap.data["snapshot"]["custom"]) == last

    resumed = await anext(stream)
    assert (resumed.type, resumed.seq) == ("log.caught_up", last)


async def test_the_stream_ends_when_the_call_does(memory_store: MemoryStore) -> None:
    """call.score seals the log and closes the fanout: every live reader finishes, none hangs."""
    log = a_log(memory_store)
    stream = log.stream()
    assert (await anext(stream)).type == "log.caught_up"
    await log.append("call.score", {"passed": True, "judges": [], "judge_calls": 0})
    assert (await anext(stream)).type == "call.score"
    with pytest.raises(StopAsyncIteration):
        await anext(stream)


async def test_whole_reads_every_page_of_a_call_and_starts_from_the_cursor(
    memory_store: MemoryStore,
) -> None:
    """The read four callers share: page by page until a short page, above whatever cursor."""
    log = a_log(memory_store)
    for n in range(5):
        await log.append("custom", a_note(n))
    assert [entry.seq for entry in await replay.whole(memory_store, CALL, limit=2)] == [
        1,
        2,
        3,
        4,
        5,
    ]
    assert [entry.seq for entry in await replay.whole(memory_store, CALL, after=3)] == [4, 5]
    assert await replay.whole(memory_store, "CA_nobody") == []
