"""Tests for the minute's count of requests per name: the limit, the wait, the minute that turns."""

from pinecall.tenancy.throttle import SWEEP_AT, Window


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
