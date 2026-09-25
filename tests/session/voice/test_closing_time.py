"""A voice call's clock: told to close a minute before its limit, and ended at it as a timeout."""

from __future__ import annotations

import pytest

from pinecall.session.voice import closing_time
from pinecall.session.voice.closing_time import CLOSING
from pinecall_protocol.defs import EndedBy, EndReason

pytestmark = pytest.mark.unit


class Ended:
    """How the clock ended the call, as the bridge's ending would record it."""

    def __init__(self) -> None:
        self.calls: list[tuple[EndReason, EndedBy, bool]] = []

    async def hangup(
        self, reason: EndReason, by: EndedBy = "agent", *, at_once: bool = False
    ) -> None:
        self.calls.append((reason, by, at_once))

    def transferred(self) -> None:
        raise AssertionError("the clock never transfers")


class Live:
    """The session, as far as the clock asks of it: a turn with an instruction."""

    def __init__(self) -> None:
        self.told: list[str] = []

    def generate_reply(self, *, instructions: str) -> object:
        self.told.append(instructions)
        return None


class Clock:
    """Sleeps that take no time and are written down, so a test reads when each thing happened."""

    def __init__(self) -> None:
        self.slept: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)


async def _kept(limit: int, *, taken: bool = False) -> tuple[Live, Ended, Clock]:
    live, ended, clock = Live(), Ended(), Clock()
    await closing_time.keep(limit, live, ended, lambda: taken, clock.sleep)
    return live, ended, clock


async def test_the_agent_is_told_a_minute_before_and_the_call_ends_at_the_limit() -> None:
    live, ended, clock = await _kept(600)
    assert clock.slept == [540, 60]
    assert live.told == [CLOSING]
    # The sentence being said is let finish: a timeout is not a supervisor's cut.
    assert ended.calls == [("timeout", "platform", False)]


async def test_a_limit_under_two_minutes_is_told_at_its_half() -> None:
    _live, ended, clock = await _kept(90)
    assert clock.slept == [45, 45]
    assert ended.calls == [("timeout", "platform", False)]


async def test_a_person_on_the_line_is_not_talked_over_and_the_limit_still_holds() -> None:
    live, ended, _clock = await _kept(600, taken=True)
    assert live.told == []
    assert ended.calls == [("timeout", "platform", False)]


async def test_no_limit_keeps_no_clock() -> None:
    live, ended, clock = await _kept(0)
    assert (clock.slept, live.told, ended.calls) == ([], [], [])


# ── which limit the clock keeps ─────────────────────────────────────────────────


def test_the_ceiling_is_the_agents_own_when_the_orgs_minutes_are_not_limited() -> None:
    assert closing_time.the_ceiling(600, None) == 600


def test_the_orgs_minutes_end_the_call_first_when_fewer_are_left_than_the_agents_limit() -> None:
    assert closing_time.the_ceiling(600, 90) == 90
    assert closing_time.the_ceiling(600, 3_000) == 600


def test_an_agent_with_no_limit_still_ends_where_the_orgs_minutes_do() -> None:
    """A written visit is handed no limit of its own, and minutes still are its length."""
    assert closing_time.the_ceiling(0, 45) == 45
    assert closing_time.the_ceiling(0, None) == 0
