"""The bridge's one hand on the log: every entry in order, once; a refusal never ends the call."""

from __future__ import annotations

import asyncio
from typing import Any, override

import pytest

from pinecall.session.voice.log_writer import Writing
from pinecall_protocol.events import AgentStateChanged
from pinecall_testkit.fake_platform import CALL, Recording

pytestmark = pytest.mark.unit


async def test_entries_queued_from_sync_callbacks_reach_the_platform_in_that_order() -> None:
    recording = Recording()
    writing = Writing(recording, CALL)
    writing.open()
    for state in ("thinking", "speaking", "idle"):
        writing.later("agent.state", AgentStateChanged(state=state))  # pyright: ignore[reportArgumentType]
    await writing.emit("agent.state", AgentStateChanged(state="listening"), ephemeral=True)
    await writing.close()
    assert [entry.data["state"] for entry in recording.entries] == [
        "thinking",
        "speaking",
        "idle",
        "listening",
    ]
    assert recording.entries[-1].ephemeral is True


async def test_a_platform_that_refuses_one_entry_is_remembered_and_the_rest_still_go() -> None:
    recording = Recording(refuse=lambda _type, data: data.get("state") == "speaking")
    writing = Writing(recording, CALL)
    writing.open()
    writing.later("agent.state", AgentStateChanged(state="speaking"))
    writing.later("agent.state", AgentStateChanged(state="idle"))
    await writing.close()
    assert writing.refused == ["agent.state"]
    assert [entry.data["state"] for entry in recording.entries] == ["idle"]


class _Away(Recording):
    """A platform that never answers an append: a gateway gone at hang-up."""

    @override
    async def append(self, *args: Any, **kwargs: Any) -> None:
        await asyncio.Event().wait()


async def test_a_close_past_its_budget_lets_the_queue_go_rather_than_wait_for_ever() -> None:
    """A seal the gateway's reaper finishes beats a job the worker has to kill at SEALING_S."""
    writing = Writing(_Away(), CALL)
    writing.open()
    writing.later("agent.state", AgentStateChanged(state="idle"))
    async with asyncio.timeout(2.0):
        await writing.close(within_s=0.05)
    assert writing.refused == ["agent.state"], "the entry that never landed is counted as lost"
