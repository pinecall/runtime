"""A declared tool as livekit runs it: out to the app through the platform, and the read-back."""

from __future__ import annotations

import pytest
from livekit.agents.llm import ToolError

from pinecall.session.voice.tools import Tools, rendered
from pinecall_protocol.defs import ToolResult
from pinecall_protocol.events import ToolCall
from tests.session.voice.fakes import BOOK, CALL, CLARA, FIND, Recording

pytestmark = pytest.mark.unit


def a_use(name: str, **arguments: object) -> ToolCall:
    return ToolCall(call_id="tu_1", name=name, arguments=dict(arguments), speech_id="sp_1")


def test_every_declared_tool_becomes_a_raw_schema_tool_under_its_own_name() -> None:
    tools = Tools(CLARA, Recording(), CALL)
    assert [tool.info.name for tool in tools.visible] == ["find_slot", "book"]  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue]
    assert [tool.info.name for tool in tools.declared((BOOK,))] == ["book"]  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue]


async def test_a_tool_round_trips_through_the_platform_under_the_agent_and_the_call() -> None:
    recording = Recording(
        tools={"find_slot": ToolResult(call_id="", name="find_slot", output="10:15")}
    )
    tools = Tools(CLARA, recording, CALL)
    assert await tools.ran(FIND, a_use("find_slot")) == "10:15"
    (asked,) = recording.asked
    assert (asked.call, asked.agent, asked.timeout_s) == (CALL, "clinica-norte", FIND.timeout_s)
    assert asked.use == a_use("find_slot")
    assert tools.read_backs == {}


async def test_a_confirm_tool_keeps_its_read_back_rendered_from_the_call_and_the_result() -> None:
    recording = Recording(tools={"book": ToolResult(call_id="", name="book", output={"ref": "A7"})})
    tools = Tools(CLARA, recording, CALL)
    await tools.ran(BOOK, a_use("book", at="10:15"))
    assert tools.read_backs == {"tu_1": "Le reservé el turno de las 10:15, referencia A7."}


async def test_a_tool_that_failed_is_a_tool_error_the_model_reads_and_no_read_back() -> None:
    recording = Recording(tools={"book": ToolResult(call_id="", name="book", error="agenda down")})
    tools = Tools(CLARA, recording, CALL)
    with pytest.raises(ToolError, match="agenda down"):
        await tools.ran(BOOK, a_use("book", at="10:15"))
    assert tools.read_backs == {}


async def test_a_platform_that_refuses_is_a_tool_that_did_not_answer() -> None:
    with pytest.raises(ToolError, match="no app is holding"):
        await Tools(CLARA, Recording(), CALL).ran(FIND, a_use("find_slot"))


def test_a_placeholder_nobody_filled_stays_visible_instead_of_vanishing() -> None:
    result = ToolResult(call_id="x", name="book", output=None)
    assert (
        rendered("Reservé {{ at }} para {{who.name}}", {"at": "10:15"}, result)
        == "Reservé 10:15 para {{who.name}}"
    )
    assert rendered("Ref {{result}}", {}, result) == "Ref "
