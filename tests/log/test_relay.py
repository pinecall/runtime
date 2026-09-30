"""The relay: a reader on one gateway hears what any gateway wrote, whole, in order, to the end."""

import asyncio
import json
from collections.abc import AsyncIterator

import pytest

from pinecall.domain.names import JsonObject
from pinecall.log._relay import CHANNEL, FILL_AFTER_S, POLL_S, STUB_OVER_BYTES, encoded
from pinecall.log.logs import Logs
from pinecall.log.store import Store
from pinecall.process.signal import LocalSignal, RedisSignal
from pinecall.wire.frames import Entry
from tests.conftest import REDIS, came_up, postgres
from tests.log.conftest import AGENT
from tests.process.test_signal import killed

pytestmark = postgres

A_WHILE_S = 5.0

# The entries each of two writers appends while the other does the same.
EACH = 120


def note(n: int) -> JsonObject:
    return {"name": "note", "data": {"n": n}}


# Two gateways: two Logs on one store, each with its own signal, or one in-process signal.
@pytest.fixture(params=["local", "redis"])
async def two(
    request: pytest.FixtureRequest, store: Store, redis_prefix: str
) -> AsyncIterator[tuple[Logs, Logs]]:
    if request.param == "local":
        signal = LocalSignal()
        pair = Logs(store, signal), Logs(store, signal)
        yield pair
        await pair[0].close()
        await pair[1].close()
        return
    if not REDIS:
        pytest.skip("PINECALL_REDIS_URL: a Redis, `make test`")
    first, second = RedisSignal(REDIS, prefix=redis_prefix), RedisSignal(REDIS, prefix=redis_prefix)
    first.start()
    second.start()
    await came_up(first)
    await came_up(second)
    pair = Logs(store, first), Logs(store, second)
    yield pair
    await pair[0].close()
    await pair[1].close()
    await first.close()
    await second.close()


async def next_of(stream: AsyncIterator[Entry]) -> Entry:
    return await asyncio.wait_for(anext(stream), A_WHILE_S)


async def test_a_reader_hears_what_both_gateways_wrote_in_seq_order_with_no_hole(
    two: tuple[Logs, Logs], call: str
) -> None:
    here, there = two
    stream = there.reading(call).stream()
    assert (await next_of(stream)).type == "log.caught_up"

    async def writes(logs: Logs, first: int) -> None:
        log = logs.writing(call, AGENT)
        for n in range(EACH):
            await log.append("custom", note(first + n))

    await asyncio.gather(writes(here, 0), writes(there, 1000))
    heard = [await next_of(stream) for _ in range(2 * EACH)]
    assert [entry.seq for entry in heard] == list(range(1, 2 * EACH + 1))
    notes = [*range(EACH), *range(1000, 1000 + EACH)]
    assert sorted(json.dumps(entry.data, sort_keys=True) for entry in heard) == sorted(
        json.dumps(note(n), sort_keys=True) for n in notes
    )


async def test_an_ephemeral_travels_and_the_terminal_entry_ends_the_reader_wherever_it_sealed(
    two: tuple[Logs, Logs], call: str
) -> None:
    here, there = two
    stream = there.reading(call).stream()
    assert (await next_of(stream)).type == "log.caught_up"
    log = here.writing(call, AGENT)
    await log.append("custom", note(1), ephemeral=True)
    heard = await next_of(stream)
    assert (heard.seq, heard.ephemeral) == (1, True)
    await log.append("call.score", {"judges": [], "judge_calls": 0, "not_judged": "test"})
    assert (await next_of(stream)).type == "call.score"
    with pytest.raises(StopAsyncIteration):
        await next_of(stream)
    assert there.reading(call).sealed


# Published out of turn, or a seq nobody stored (an ephemeral lost): the reader is given the
# entries in order all the same, the store asked for what was missing.
async def test_what_arrives_out_of_turn_or_never_is_delivered_in_order_off_the_store(
    store: Store, call: str
) -> None:
    signal = LocalSignal()
    logs = Logs(store, signal)
    stream = logs.reading(call).stream()
    assert (await next_of(stream)).type == "log.caught_up"
    written = [
        await store.append(call, AGENT, "custom", note(n), ephemeral=False) for n in range(1, 5)
    ]
    channel = CHANNEL.format(name=call)
    signal.publish(channel, encoded(written[2], "another-gateway"))
    signal.publish(channel, encoded(written[0], "another-gateway"))
    assert (await next_of(stream)).seq == 1
    heard = [await next_of(stream) for _ in range(2)]
    assert [entry.seq for entry in heard] == [2, 3]
    # Seq 5 is an ephemeral nobody stored and nobody published; 4 was stored and not published.
    lost = await store.append(call, AGENT, "custom", note(5), ephemeral=True)
    sixth = await store.append(call, AGENT, "custom", note(6), ephemeral=False)
    assert (lost.seq, sixth.seq) == (5, 6)
    signal.publish(channel, encoded(sixth, "another-gateway"))
    heard = [await next_of(stream) for _ in range(2)]
    assert [entry.seq for entry in heard] == [4, 6]
    await logs.close()


async def test_a_big_entry_travels_as_its_address_and_is_heard_whole(
    two: tuple[Logs, Logs], call: str
) -> None:
    here, there = two
    stream = there.reading(call).stream()
    assert (await next_of(stream)).type == "log.caught_up"
    big: JsonObject = {"name": "document", "data": {"text": "x" * (STUB_OVER_BYTES + 1)}}
    entry = await here.writing(call, AGENT).append("custom", big)
    stub = {"sender": "x", "stored": {"log": call, "seq": entry.seq}}
    assert json.loads(encoded(entry, "x")) == stub
    heard = await next_of(stream)
    assert (heard.seq, heard.data) == (entry.seq, big)


async def test_a_reader_whose_connection_was_lost_gets_a_gap_and_hears_again(
    store: Store, call: str, redis_prefix: str
) -> None:
    if not REDIS:
        pytest.skip("PINECALL_REDIS_URL: a Redis, `make test`")
    first, second = RedisSignal(REDIS, prefix=redis_prefix), RedisSignal(REDIS, prefix=redis_prefix)
    first.start()
    second.start()
    await came_up(first)
    await came_up(second)
    here, there = Logs(store, first), Logs(store, second)
    try:
        # Two readers of one log: both are dropped, and both hear again on a fresh subscription.
        streams = [there.reading(call).stream() for _ in range(2)]
        for stream in streams:
            assert (await next_of(stream)).type == "log.caught_up"
        await here.writing(call, AGENT).append("custom", note(1))
        for stream in streams:
            assert (await next_of(stream)).seq == 1
        assert await killed(f"pinecall-{second.id}") == 1
        for stream in streams:
            assert (await next_of(stream)).type == "log.gap"
            assert (await next_of(stream)).type == "log.caught_up"
        await here.writing(call, AGENT).append("custom", note(2))
        for stream in streams:
            assert (await next_of(stream)).seq == 2
    finally:
        await here.close()
        await there.close()
        await first.close()
        await second.close()


# No Redis answers: durable entries still reach the reader, off the store, within a second.
async def test_with_the_signal_down_a_reader_is_fed_off_the_store(store: Store, call: str) -> None:
    nobody = RedisSignal("redis://127.0.0.1:1/1")
    nobody.start()
    here, there = Logs(store, LocalSignal()), Logs(store, nobody)
    try:
        stream = there.reading(call).stream()
        assert (await next_of(stream)).type == "log.caught_up"
        log = here.writing(call, AGENT)
        for n in range(1, 4):
            await log.append("custom", note(n))
        heard = [await asyncio.wait_for(anext(stream), 3 * POLL_S) for _ in range(3)]
        assert [entry.seq for entry in heard] == [1, 2, 3]
    finally:
        await here.close()
        await there.close()
        await nobody.close()


def test_the_fill_waits_less_than_a_turn_of_a_call() -> None:
    assert FILL_AFTER_S < 1.0 < POLL_S + 1


# An org's feed and the box's floor hear a call opened on another gateway, as it happens.
async def test_a_feed_and_the_box_on_one_gateway_hear_what_another_wrote(
    two: tuple[Logs, Logs], store: Store, call: str
) -> None:
    here, there = two
    await store.claim(call, AGENT, "org_a")
    feed = await there.feed_reader("org_a", "production")
    box = await there.box_reader()
    ringing: JsonObject = {
        "channel": "phone",
        "from": "+34600111222",
        "to": "+34900000000",
        "route": {"channel": "phone", "number": "+34900000000"},
        "caller": None,
    }
    await here.writing(call, AGENT).append("call.ringing", ringing)
    assert (await asyncio.wait_for(anext(feed), A_WHILE_S)).call == call
    assert (await asyncio.wait_for(anext(box), A_WHILE_S)).call == call
    feed.close()
    box.close()
    assert there.relay.followed == 0
