"""Tests for the minute's count of requests per name: the limit, the wait, the minute that turns."""

import asyncio

from pinecall.process.signal import LocalSignal
from pinecall.tenancy.throttle import PACED_CHANNEL, SAID_FROM, SWEEP_AT, Window


class Clock:
    """A clock a test moves by hand."""

    def __init__(self, now: float) -> None:
        """The clock at this moment."""
        self.now = now

    def __call__(self) -> float:
        """The moment it shows."""
        return self.now


async def test_the_request_past_the_limit_waits_until_the_minute_turns() -> None:
    clock = Clock(600.0 + 45.0)
    window = Window(clock)
    assert [await window.counted("org_1/sandbox/calls", 2) for _ in range(2)] == [None, None]
    assert await window.counted("org_1/sandbox/calls", 2) == 15.0
    assert await window.counted("org_2/sandbox/calls", 2) is None
    clock.now = 660.0
    assert await window.counted("org_1/sandbox/calls", 2) is None


async def test_the_names_of_minutes_gone_are_dropped_once_there_are_many() -> None:
    clock = Clock(0.0)
    window = Window(clock)
    for number in range(SWEEP_AT):
        await window.counted(f"org_{number}", 10)
    clock.now = 60.0
    await window.counted("org_now", 10)
    assert list(window.counts) == ["org_now"]


# A name busy on one gateway is said to the other within a second, and the wall is the sum.
async def test_a_names_requests_on_two_gateways_are_summed_against_its_limit() -> None:
    clock, signal = Clock(600.0), LocalSignal()
    here, there = Window(clock, signal), Window(clock, signal)
    here.shared.every_s = there.shared.every_s = 0.01
    await here.start()
    await there.start()
    shares = await signal.subscribe(PACED_CHANNEL)
    for _ in range(SAID_FROM):
        assert await here.counted("org_1/sandbox/calls", SAID_FROM + 5) is None
    async with asyncio.timeout(2):
        while not any(heard.share.counts for heard in there.shared.theirs.values()):
            await anext(shares)
            await asyncio.sleep(0)
    shares.close()
    assert [await there.counted("org_1/sandbox/calls", SAID_FROM + 5) for _ in range(5)] == [
        None
    ] * 5
    assert await there.counted("org_1/sandbox/calls", SAID_FROM + 5) == 60.0
    await here.close()
    await there.close()
