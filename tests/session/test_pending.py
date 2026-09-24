"""The tool calls in flight: one round trip per call_id, and the ones still waiting, in order."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from pinecall.log.entry import Entry
from pinecall.session.declaring import ToolUse
from pinecall.session.pending import ToolCalls
from pinecall.types import AgentConfig
from pinecall_protocol import WireModel, defs, encode

pytestmark = pytest.mark.unit


class Written:
    """The session's hand on the log: every entry numbered, as a store would."""

    def __init__(self) -> None:
        self.entries: list[Entry] = []

    async def emit(self, type: str, event: WireModel) -> Entry:
        """One entry written, with the next seq."""
        entry = Entry(
            seq=len(self.entries) + 1,
            ts=time.time(),
            call="call_1",
            agent="clinica",
            type=type,
            ephemeral=False,
            data=encode(event),
        )
        self.entries.append(entry)
        return entry

    def types(self) -> list[str]:
        """What was written, by type."""
        return [entry.type for entry in self.entries]


def a_use(call_id: str) -> ToolUse:
    """The model asking for one tool."""
    return ToolUse(call_id=call_id, name="find_slots", arguments={"day": "martes"})


async def settled() -> None:
    """Let every task that can move, move."""
    for _ in range(5):
        await asyncio.sleep(0)


async def test_a_tool_asked_twice_by_call_id_is_written_once_and_answered_once() -> None:
    calls, log = ToolCalls(AgentConfig(slug="clinica")), Written()
    first = asyncio.ensure_future(calls.ran(a_use("tu_1"), "sp_1", log.emit))
    await settled()
    again = asyncio.ensure_future(calls.ran(a_use("tu_1"), "sp_1", log.emit))
    await settled()
    calls.answered(defs.ToolResult(call_id="tu_1", name="find_slots", output="10:15"))
    answers: list[Any] = [await first, await again]
    assert [answer.output for answer in answers] == ["10:15", "10:15"]
    assert log.types() == ["tool.call", "tool.result"]


async def test_the_tools_still_waiting_are_the_entries_that_went_out_in_order() -> None:
    calls, log = ToolCalls(AgentConfig(slug="clinica")), Written()
    running = [
        asyncio.ensure_future(calls.ran(a_use(call_id), "sp_1", log.emit))
        for call_id in ("tu_1", "tu_2")
    ]
    await settled()
    assert [entry.data["call_id"] for entry in calls.pending()] == ["tu_1", "tu_2"]
    calls.answered(defs.ToolResult(call_id="tu_1", name="find_slots", output="10:15"))
    await running[0]
    assert [entry.data["call_id"] for entry in calls.pending()] == ["tu_2"]
    calls.answered(defs.ToolResult(call_id="tu_2", name="find_slots", output="11:00"))
    await running[1]
    assert calls.pending() == ()
