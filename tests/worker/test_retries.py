"""Asking the gateway again while it is away: which refusals wait, how long, and when it stops."""

from __future__ import annotations

import itertools
from collections.abc import Awaitable, Callable

import pytest

from pinecall.worker import retries
from pinecall.worker.gateway_http import GatewayRefused
from pinecall.worker.retries import again, away, delays

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """The waits, taken down instead of slept."""
    waited: list[float] = []

    async def slept(seconds: float) -> None:
        waited.append(seconds)

    monkeypatch.setattr(retries.asyncio, "sleep", slept)
    return waited


def refusing(*statuses: int | None) -> tuple[list[int], Callable[[], Awaitable[str]]]:
    """An attempt that is refused with these statuses, in order, then answers."""
    asked: list[int] = []
    answers = iter(statuses)

    async def attempt() -> str:
        asked.append(1)
        for status in answers:
            raise GatewayRefused(f"POST /x: {status}", status)
        return "answered"

    return asked, attempt


def test_the_delays_double_to_the_cap() -> None:
    assert list(itertools.islice(delays(0.5, 5.0), 6)) == [0.5, 1.0, 2.0, 4.0, 5.0, 5.0]


def test_a_gateway_away_is_unreachable_or_a_5xx_and_a_4xx_is_an_answer() -> None:
    assert away(GatewayRefused("unreachable")) and away(GatewayRefused("502", 502))
    assert not away(GatewayRefused("404", 404)) and not away(GatewayRefused("409", 409))


async def test_an_attempt_is_asked_again_while_the_gateway_is_away(no_waiting: list[float]) -> None:
    asked, attempt = refusing(None, 502, 503)
    assert await again(attempt, within_s=None, what="POST /x") == "answered"
    assert len(asked) == 4 and no_waiting == [0.5, 1.0, 2.0]


async def test_a_4xx_is_final_and_not_asked_again() -> None:
    asked, attempt = refusing(409)
    with pytest.raises(GatewayRefused, match="409"):
        await again(attempt, within_s=None, what="POST /x")
    assert len(asked) == 1


async def test_an_attempt_with_a_deadline_stops_asking_at_it() -> None:
    asked, attempt = refusing(*([None] * 50))
    with pytest.raises(GatewayRefused):
        await again(attempt, within_s=0.0, what="POST /x")
    assert len(asked) == 1
