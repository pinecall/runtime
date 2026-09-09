"""The bridge's one hand on the log: every entry in order, once; a refusal never ends the call."""

from __future__ import annotations

import pytest

from pinecall.session.voice.writing import Writing
from pinecall_protocol.events import AgentStateChanged
from tests.session.voice.fakes import CALL, Recording

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
