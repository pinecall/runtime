"""One conversation's clock: quiet is counted from the last answer, never from the last message."""

from __future__ import annotations

import asyncio
from typing import cast

import pytest

from pinecall.api.whatsapp.threads import WENT_QUIET, Thread
from pinecall.session.text.session import TextSession
from pinecall_protocol.defs import EndReason

pytestmark = pytest.mark.unit

# Short enough that a turn held open by the test outlasts it many times over.
AN_IDLE_PERIOD_S = 0.01
# A bound on a hang, never a clock the outcome depends on.
WAITS_S = 5.0


class _AHeldTurn:
    """A session whose one turn lasts exactly as long as the test says: no clock inside."""

    call = "call_held"
    agent = "clara"

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.may_finish = asyncio.Event()
        self.answered: list[str] = []

    async def hears(self, text: str) -> None:
        self.started.set()
        await self.may_finish.wait()
        self.answered.append(text)


async def test_a_turn_slower_than_the_idle_period_is_answered_and_not_cut_off() -> None:
    session = _AHeldTurn()
    closed: list[EndReason] = []
    ended = asyncio.Event()

    async def closing(_thread: Thread, reason: EndReason) -> None:
        closed.append(reason)
        ended.set()

    thread = Thread(cast("TextSession", session), "pnid", closing, AN_IDLE_PERIOD_S)
    thread.heard("hola")
    await asyncio.wait_for(session.started.wait(), WAITS_S)
    # Many idle periods pass with the turn still running: nothing is quiet yet, so nothing closes.
    for _ in range(10):
        await asyncio.sleep(AN_IDLE_PERIOD_S)
    assert closed == []
    session.may_finish.set()
    await asyncio.wait_for(ended.wait(), WAITS_S)
    assert session.answered == ["hola"]
    assert closed == [WENT_QUIET]
