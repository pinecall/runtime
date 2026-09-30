"""Tests for the signal: a round trip, a listener cut off or left behind, Redis away, alone."""

import asyncio
from collections.abc import AsyncIterator
from urllib.parse import urlsplit

import pytest

from pinecall.domain.errors import NotAvailable, SettingsRefused
from pinecall.process.signal import (
    LISTENING_DEPTH,
    UNSENT_AT_MOST,
    Listening,
    LocalSignal,
    RedisSignal,
    Signal,
    opened_signal,
)
from tests.conftest import REDIS, came_up, redis, settings_of

# Well past the depth, so a listener that reads nothing is dropped and one that reads keeps up.
MANY = LISTENING_DEPTH + 40

# What the fast listener is handed between two waits: well under the depth.
A_STRETCH = 32

# Nothing listens on the discard port of the loopback.
NOBODY = "redis://127.0.0.1:9/0"


@pytest.fixture(params=["in this process", "on redis"])
async def signal(request: pytest.FixtureRequest, redis_prefix: str) -> AsyncIterator[Signal]:
    """The same signal twice: inside this process, and on the suites' Redis when there is one."""
    if request.param == "in this process":
        local = LocalSignal()
        yield local
        await local.close()
        return
    if not REDIS:
        pytest.skip("PINECALL_REDIS_URL: a Redis, `make test`")
    on_redis = RedisSignal(REDIS, prefix=redis_prefix)
    on_redis.start()
    await came_up(on_redis)
    yield on_redis
    await on_redis.close()


async def heard(listening: Listening, count: int) -> list[bytes]:
    """The next messages of a listener, failing the test when they do not come."""
    found: list[bytes] = []
    async with asyncio.timeout(5):
        async for data in listening:
            found.append(data)
            if len(found) == count:
                break
    return found


async def test_what_is_published_reaches_every_listener_of_its_channel_in_order(
    signal: Signal,
) -> None:
    first, second = await signal.subscribe("log:a"), await signal.subscribe("log:a")
    other = await signal.subscribe("log:b")
    for number in range(3):
        signal.publish("log:a", f"a{number}".encode())
    signal.publish("log:b", b"b0")
    assert await heard(first, 3) == [b"a0", b"a1", b"a2"]
    assert await heard(second, 3) == [b"a0", b"a1", b"a2"]
    assert await heard(other, 1) == [b"b0"]


async def test_a_listener_closed_ends_and_the_others_on_its_channel_go_on(signal: Signal) -> None:
    leaving, staying = await signal.subscribe("log:a"), await signal.subscribe("log:a")
    leaving.close()
    signal.publish("log:a", b"after")
    assert await heard(staying, 1) == [b"after"]
    assert [data async for data in leaving] == []
    assert not leaving.dropped


async def test_a_listener_that_falls_behind_is_dropped_and_never_holds_up_the_publisher(
    signal: Signal,
) -> None:
    slow, fast = await signal.subscribe("log:a"), await signal.subscribe("log:a")
    fast_heard: list[bytes] = []
    for start in range(0, MANY, A_STRETCH):
        stretch = range(start, min(start + A_STRETCH, MANY))
        for number in stretch:
            # Returns at once: nothing the publisher does waits for a listener.
            assert signal.publish("log:a", str(number).encode()) is None
        fast_heard += await heard(fast, len(stretch))
    assert fast_heard == [str(number).encode() for number in range(MANY)]
    kept = [data async for data in slow]
    assert slow.dropped
    assert kept == [str(number).encode() for number in range(LISTENING_DEPTH)]


@redis
async def test_two_processes_on_one_redis_hear_each_other(
    redis_signal: RedisSignal, redis_prefix: str
) -> None:
    other = RedisSignal(REDIS, prefix=redis_prefix)
    other.start()
    try:
        await came_up(other)
        listening = await other.subscribe("log:a")
        redis_signal.publish("log:a", b"from the first")
        assert await heard(listening, 1) == [b"from the first"]
    finally:
        await other.close()


@redis
async def test_a_lost_connection_drops_its_listeners_and_the_signal_comes_back(
    redis_signal: RedisSignal,
) -> None:
    cut_off = await redis_signal.subscribe("log:a")
    assert await killed(f"pinecall-{redis_signal.id}") == 1
    assert [data async for data in cut_off] == []
    assert cut_off.dropped
    await came_up(redis_signal)
    again = await redis_signal.subscribe("log:a")
    redis_signal.publish("log:a", b"after it came back")
    assert await heard(again, 1) == [b"after it came back"]


async def test_a_signal_nobody_answers_refuses_a_listener_and_publishes_into_nothing() -> None:
    away = RedisSignal(NOBODY)
    away.start()
    try:
        assert not away.up
        with pytest.raises(NotAvailable, match="PINECALL_REDIS_URL"):
            await away.subscribe("log:a")
        # Never raises nor waits, and holds at most so many: the oldest go first.
        for number in range(UNSENT_AT_MOST + 5):
            away.publish("log:a", str(number).encode())
        assert away.dropped >= 5
    finally:
        await away.close()


def test_an_address_that_is_not_redis_is_refused_when_the_process_starts() -> None:
    with pytest.raises(SettingsRefused, match="PINECALL_REDIS_URL"):
        RedisSignal("http://127.0.0.1:6379")


async def test_a_process_given_no_redis_keeps_the_signal_to_itself() -> None:
    async with opened_signal(settings_of()) as alone:
        assert isinstance(alone, LocalSignal)
        assert alone.up
    wanted = settings_of().model_copy(update={"redis_url": NOBODY})
    async with opened_signal(wanted) as shared:
        assert isinstance(shared, RedisSignal)


# The listening connection alone: the one whose client list flags say it is subscribed.
async def killed(name: str) -> int:
    """Close the named process's listening connection from Redis's side; how many it closed."""
    address = urlsplit(REDIS)
    reader, writer = await asyncio.open_connection(address.hostname, address.port or 6379)
    try:
        listed = (await _answer_to(reader, writer, "CLIENT LIST")).decode()
        ids = [
            fields["id"]
            for line in listed.splitlines()
            if (fields := dict(pair.split("=", 1) for pair in line.split() if "=" in pair))
            if fields.get("name") == name and "P" in fields.get("flags", "")
        ]
        for number in ids:
            await _answer_to(reader, writer, f"CLIENT KILL ID {number}")
        return len(ids)
    finally:
        writer.close()
        await writer.wait_closed()


async def _answer_to(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, command: str
) -> bytes:
    writer.write(f"{command}\r\n".encode())
    await writer.drain()
    head = (await reader.readline()).rstrip(b"\r\n")
    if head.startswith(b"$"):
        return (await reader.readexactly(int(head[1:]) + 2))[:-2]
    return head[1:]
