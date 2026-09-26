"""The bridge's close ends, whatever the platform did: the verdict is the one thing it may cost."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import override

import pytest

from pinecall._settings import Budgets
from pinecall.session.voice.bridge import a_bridge
from pinecall.session.voice.sealing import THE_LOG_NEVER_ARRIVED
from pinecall.types.json import JsonObject
from tests.session.voice.fakes import CLARA, Recording
from tests.session.voice.fakes import a_call as a_context

pytestmark = pytest.mark.unit


class _NeverReadBack(Recording):
    """A platform that takes every entry and never answers the read of the log."""

    @override
    async def since(self, call: str, after: int) -> AsyncIterator[JsonObject]:
        await asyncio.Event().wait()
        yield {}  # pragma: no cover — never reached


async def test_a_platform_that_cannot_be_read_back_costs_the_verdict_and_never_the_seal() -> None:
    """The read of the log is bounded by the seal's budget; the entries before it still land."""
    recording = _NeverReadBack()
    bridge = a_bridge(a_context(), CLARA, recording, budgets=Budgets(seal_s=0.1))
    bridge.writing.open()
    async with asyncio.timeout(2.0):
        await bridge.closed("the test hung up")
    assert recording.types[-3:] == ["call.ended", "call.summary", "call.score"]
    (score,) = recording.of("call.score")
    assert score.data["not_judged"] == THE_LOG_NEVER_ARRIVED
    assert score.data.get("passed") is None
