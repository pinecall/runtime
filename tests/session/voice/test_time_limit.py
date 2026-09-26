"""A voice call's clock: told to close a minute before its limit, and ended at it as a timeout."""

from __future__ import annotations

import pytest

from pinecall.session.voice import time_limit
from pinecall.session.voice.time_limit import CLOSING
from pinecall.types.org import Ceiling
from pinecall_protocol.defs import EndedBy, EndReason
from pinecall_protocol.events import CreditsExhausted

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
    await time_limit.keep(limit, live, ended, lambda: taken, clock.sleep)
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
    assert time_limit.the_ceiling(600, None) == 600


def test_the_orgs_minutes_end_the_call_first_when_fewer_are_left_than_the_agents_limit() -> None:
    assert time_limit.the_ceiling(600, 90) == 90
    assert time_limit.the_ceiling(600, 3_000) == 600


def test_an_agent_with_no_limit_still_ends_where_the_orgs_minutes_do() -> None:
    """A written visit is handed no limit of its own, and minutes still are its length."""
    assert time_limit.the_ceiling(0, 45) == 45
    assert time_limit.the_ceiling(0, None) == 0


# ── why it ends ─────────────────────────────────────────────────────────────────


def test_the_orgs_minutes_ending_the_call_first_are_written_as_credits_exhausted() -> None:
    clock = time_limit.the_clock(600, Ceiling(seconds=90, minutes=30), "clinica")
    assert clock.limit_s == 90
    assert clock.exhausted == CreditsExhausted(org="clinica", quota="minutes", used=30, limit=30)


def test_the_agents_own_limit_coming_first_ends_the_call_with_no_refusal() -> None:
    assert time_limit.the_clock(600, Ceiling(seconds=900, minutes=30), "clinica").exhausted is None
    assert time_limit.the_clock(600, Ceiling(seconds=600, minutes=30), "clinica").exhausted is None
    assert time_limit.the_clock(600, None, "clinica") == time_limit.Clock(600)


def test_a_written_visit_is_ended_by_the_minutes_alone_and_says_so() -> None:
    clock = time_limit.the_clock(0, Ceiling(seconds=45, minutes=30), "clinica")
    assert (clock.limit_s, clock.exhausted is not None) == (45, True)


async def test_the_refusal_is_written_before_the_call_ends_and_not_after() -> None:
    order: list[str] = []
    ended = Ended()

    async def written() -> None:
        order.append("credits.exhausted")

    async def hangup(reason: EndReason, by: EndedBy = "agent", *, at_once: bool = False) -> None:  # noqa: ARG001
        order.append(reason)

    ended.hangup = hangup  # type: ignore[method-assign]
    await time_limit.keep(90, Live(), ended, lambda: False, Clock().sleep, before_the_end=written)
    assert order == ["credits.exhausted", "timeout"]
