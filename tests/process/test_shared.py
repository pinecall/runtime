"""A share of each gateway: said on the signal, heard by the others, forgotten when silent."""

import asyncio
import time
from collections.abc import AsyncIterator

import pytest
from pydantic import TypeAdapter

from pinecall.process import shared
from pinecall.process.shared import Assigned, Shared, newest
from pinecall.process.signal import LocalSignal

NUMBERS: TypeAdapter[list[int]] = TypeAdapter(list[int])


@pytest.fixture
async def pair() -> AsyncIterator[tuple[Shared[list[int]], Shared[list[int]]]]:
    signal = LocalSignal()
    changes: list[str] = []
    first = Shared(signal, "numbers", NUMBERS, [], lambda: changes.append("first"))
    second = Shared(signal, "numbers", NUMBERS, [], lambda: changes.append("second"))
    await first.start()
    await second.start()
    yield first, second
    await first.close()
    await second.close()


async def settled() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


async def test_a_share_said_is_heard_by_the_other_and_never_by_itself(
    pair: tuple[Shared[list[int]], Shared[list[int]]],
) -> None:
    first, second = pair
    await settled()
    first.put([1, 2])
    await settled()
    assert [heard.share for heard in second.theirs.values()] == [[1, 2]]
    assert first.id not in first.theirs
    second.put([3])
    await settled()
    assert [heard.share for heard in first.theirs.values()] == [[3]]


async def test_a_process_silent_too_long_is_forgotten(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shared, "TOLD_EVERY_S", 0.02)
    monkeypatch.setattr(shared, "SILENT_AT_MOST_S", 0.1)
    signal = LocalSignal()
    moved = asyncio.Event()
    first = Shared(signal, "numbers", NUMBERS, [], lambda: None)
    second = Shared(signal, "numbers", NUMBERS, [], moved.set)
    await first.start()
    await second.start()
    first.put([1])
    await settled()
    assert second.theirs
    await first.close()
    async with asyncio.timeout(2):
        while second.theirs:
            moved.clear()
            await moved.wait()
    await second.close()


def test_the_newest_setting_of_a_key_stands_and_ours_overruled_is_dropped() -> None:
    now = time.time()
    mine = {("sandbox", "agent"): Assigned("sandbox", "agent", "m_ana", now - 5)}
    theirs = [Assigned("sandbox", "agent", "m_ben", now), Assigned("sandbox", "other", None, now)]
    assert newest(mine, theirs) == {("sandbox", "agent"): "m_ben"}
    assert mine == {}


def test_a_key_set_to_nobody_is_kept_a_while_then_let_go() -> None:
    now = time.time()
    fresh = {("sandbox", "a"): Assigned("sandbox", "a", None, now)}
    assert newest(fresh, []) == {}
    assert ("sandbox", "a") in fresh
    spent = {("sandbox", "a"): Assigned("sandbox", "a", None, now - shared.FORGOTTEN_AFTER_S - 1)}
    newest(spent, [])
    assert spent == {}
