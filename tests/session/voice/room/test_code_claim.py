"""The keypad heard for a code: four tones close together are one claim, asked once, no error."""

from __future__ import annotations

import asyncio
import logging

import pytest

from pinecall.session.voice.room.code_claim import Claiming
from tests.session.voice.fakes import CALL, Recording

pytestmark = pytest.mark.unit


async def answered() -> None:
    """Let every claim asked so far reach the platform and come back."""
    for _ in range(3):
        await asyncio.sleep(0)


def keyed(claiming: Claiming, digits: str, at: float = 100.0, apart_s: float = 1.0) -> None:
    """The caller keys these digits, one every `apart_s` seconds from `at`."""
    for n, digit in enumerate(digits):
        claiming.heard(digit, at + n * apart_s)


async def test_four_tones_within_the_window_are_one_claim() -> None:
    platform = Recording()
    platform.codes.add("4821")
    keyed(Claiming(platform, CALL), "4821")
    await answered()
    assert platform.claims == ["4821"]


async def test_tones_too_far_apart_are_not() -> None:
    platform = Recording()
    keyed(Claiming(platform, CALL), "4821", apart_s=3.0)
    await answered()
    assert platform.claims == []


async def test_the_same_code_is_not_asked_twice() -> None:
    platform = Recording()
    claiming = Claiming(platform, CALL)
    keyed(claiming, "4821")
    keyed(claiming, "4821", at=110.0)
    keyed(claiming, "4822", at=120.0)
    await answered()
    assert platform.claims == ["4821", "4822"]


async def test_a_star_or_a_pound_starts_the_code_over() -> None:
    platform = Recording()
    keyed(Claiming(platform, CALL), "48#2193")
    await answered()
    assert platform.claims == ["2193"]


async def test_a_refused_claim_is_no_error(caplog: pytest.LogCaptureFixture) -> None:
    """Nobody issued it: nothing at all. Any other refusal: one warning, and the call goes on."""
    platform = Recording()
    claiming = Claiming(platform, CALL)
    keyed(claiming, "1234")
    await answered()
    assert platform.claims == ["1234"]
    assert not caplog.records

    platform.refuses_to_claim = "POST /v1/calls/call_1/claim: 403"
    with caplog.at_level(logging.WARNING):
        keyed(claiming, "5678", at=200.0)
        await answered()
    assert [record.levelno for record in caplog.records] == [logging.WARNING]
