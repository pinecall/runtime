"""Who runs each written call and holds each thread: said by each gateway, heard by the others."""

import asyncio
from collections.abc import AsyncIterator

import pytest

from pinecall.gateway.calls.owners import Owners
from pinecall.process.signal import LocalSignal


@pytest.fixture
async def two() -> AsyncIterator[tuple[Owners, Owners]]:
    signal = LocalSignal()
    first, second = Owners(signal), Owners(signal)
    await first.start()
    await second.start()
    yield first, second
    await first.close()
    await second.close()


async def settled() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


async def test_a_written_call_and_a_thread_held_here_are_elsewhere_to_the_other(
    two: tuple[Owners, Owners],
) -> None:
    first, second = two
    await settled()
    first.running("CA_1", here=True)
    first.holding("org|sandbox|+598|598", here=True)
    await settled()
    assert second.text_elsewhere("CA_1")
    assert second.threads_elsewhere.get("org|sandbox|+598|598") == first.id
    assert not first.text_elsewhere("CA_1")
    first.running("CA_1", here=False)
    first.holding("org|sandbox|+598|598", here=False)
    await settled()
    assert not second.text_elsewhere("CA_1")
    assert second.threads_elsewhere.get("org|sandbox|+598|598") is None
