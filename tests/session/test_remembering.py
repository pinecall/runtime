"""What a hang-up asks of the platform: inside its budget, and never holding the seal."""

from __future__ import annotations

import asyncio

import pytest

from pinecall.session.remembering import (
    REMEMBER_FAILED,
    NoRememberer,
    Rememberer,
    remembered_within,
)

pytestmark = pytest.mark.unit

CALL = "call_1"


class Forgetting:
    """A rememberer that fails the way a test tells it to."""

    def __init__(self, after_s: float = 0.0, failing: Exception | None = None) -> None:
        self._after_s = after_s
        self._failing = failing
        self.remembered: list[str] = []

    async def remember(self, call: str) -> int:
        await asyncio.sleep(self._after_s)
        if self._failing is not None:
            raise self._failing
        self.remembered.append(call)
        return 1


async def test_remembering_within_the_budget_writes_nothing_to_the_log() -> None:
    rememberer = Forgetting()
    assert await remembered_within(rememberer, CALL, 1.0) is None
    assert rememberer.remembered == [CALL]


async def test_a_rememberer_past_its_budget_is_a_recoverable_entry_naming_the_budget() -> None:
    failed = await remembered_within(Forgetting(after_s=0.5), CALL, 0.05)
    assert failed is not None
    assert (failed.code, failed.recoverable) == (REMEMBER_FAILED, True)
    assert failed.message == "memory was not written: no answer within 0.05 s"


async def test_a_rememberer_that_raises_is_a_recoverable_entry_with_its_words() -> None:
    failed = await remembered_within(Forgetting(failing=RuntimeError("no model")), CALL, 1.0)
    assert failed is not None
    assert failed.message == "memory was not written: no model"


async def test_the_no_rememberer_is_a_rememberer_that_writes_nothing() -> None:
    rememberer: Rememberer = NoRememberer()
    assert await remembered_within(rememberer, CALL, 1.0) is None
