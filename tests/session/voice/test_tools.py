"""A declared tool as livekit runs it: out to the app through the platform, and the read-back."""

from __future__ import annotations

import json
from typing import Any, override

import pytest
from livekit.agents import llm as agents
from livekit.agents.llm import ToolError
from livekit.agents.voice import AgentSession

from pinecall.session.voice import a_bridge
from pinecall.session.voice.tools import Tools, rendered
from pinecall_protocol import WireModel, defs, encode
from pinecall_protocol.defs import ToolResult
from pinecall_protocol.events import ToolCall
from tests.session.fake_llm import FakeLLM, Scripted
from tests.session.voice.fakes import BOOK, CALL, CLARA, FIND, Recording
from tests.session.voice.fakes import a_call as a_context

pytestmark = pytest.mark.unit


def a_use(name: str, **arguments: object) -> ToolCall:
    return ToolCall(call_id="tu_1", name=name, arguments=dict(arguments), speech_id="sp_1")


class Emitted:
    """The bridge's hand on the log, as the tools reach it: every entry, in order."""

    def __init__(self) -> None:
        self.entries: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, type: str, event: WireModel) -> None:
        self.entries.append((type, encode(event)))


def a_tools(recording: Recording, emitted: Emitted | None = None) -> Tools:
    """The clinic's tools on a scripted platform, writing wherever the test is reading."""
    return Tools(CLARA, recording, CALL, emitted or Emitted())


def test_every_declared_tool_becomes_a_raw_schema_tool_under_its_own_name() -> None:
    tools = a_tools(Recording())
    assert [tool.info.name for tool in tools.declared_tools] == ["find_slot", "book"]  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue]


async def test_a_tool_the_app_closed_is_refused_before_the_app_and_the_model_reads_why() -> None:
    """tools.set narrowed the state to find_slot: a book the model tries anyway never leaves."""
    recording = Recording(tools={"book": ToolResult(call_id="", name="book", output={"ref": "A7"})})
    emitted = Emitted()
    tools = a_tools(recording, emitted)
    tools.visibility.narrow([defs.ToolSpec(name="find_slot", description="", parameters={})])
    with pytest.raises(ToolError, match="book is not available now"):
        await tools.ran(BOOK, a_use("book", at="10:15"))
    assert recording.asked == []
    assert tools.read_backs == {}
    assert [type for type, _data in emitted.entries] == ["error"]
    assert emitted.entries[0][1]["code"] == "refused"
    assert emitted.entries[0][1]["message"] == "book is not available now"


async def test_a_tool_the_app_left_open_still_goes_through() -> None:
    recording = Recording(
        tools={"find_slot": ToolResult(call_id="", name="find_slot", output="10:15")}
    )
    emitted = Emitted()
    tools = a_tools(recording, emitted)
    tools.visibility.narrow([defs.ToolSpec(name="find_slot", description="", parameters={})])
    assert await tools.ran(FIND, a_use("find_slot")) == "10:15"
    assert [asked.use.name for asked in recording.asked] == ["find_slot"]
    assert emitted.entries == []


async def test_a_tools_set_between_two_requests_leaves_the_providers_tools_byte_identical() -> None:
    """What the cache is for: tools come first in the prefix, and a re-declared one empties it."""
    llm = FakeLLM(Scripted(chunks=("Uno.",)), Scripted(chunks=("Dos.",)))
    recording = Recording()
    bridge = a_bridge(a_context(), CLARA, recording)
    live: AgentSession[None] = AgentSession(
        llm=llm, vad=None, turn_handling={"turn_detection": "manual"}
    )
    await bridge.opened(live)
    await live.start(bridge.agent, record=False)  # pyright: ignore[reportUnknownMemberType]
    await live.generate_reply(user_input="hola")
    await bridge.set_tools([defs.ToolSpec(name="book", description="", parameters={})])
    await live.generate_reply(user_input="reservame")
    await live.aclose()
    first, second = llm.asked
    assert _the_tools_the_provider_reads(first) == _the_tools_the_provider_reads(second)
    assert first.tools == second.tools == ("find_slot", "book")
    (changed,) = recording.of("tools.changed")
    assert changed.data["visible"] == ["book"]


def _the_tools_the_provider_reads(asked: Any) -> str:
    """The tool schemas as the Anthropic plugin builds them (anthropic/llm.py:184), as bytes."""
    schemas: Any = agents.ToolContext(list(asked.declared)).parse_function_tools("anthropic")  # pyright: ignore[reportUnknownMemberType]
    return json.dumps(schemas, sort_keys=True)


async def test_a_tool_round_trips_through_the_platform_under_the_agent_and_the_call() -> None:
    recording = Recording(
        tools={"find_slot": ToolResult(call_id="", name="find_slot", output="10:15")}
    )
    tools = a_tools(recording)
    assert await tools.ran(FIND, a_use("find_slot")) == "10:15"
    (asked,) = recording.asked
    assert (asked.call, asked.agent, asked.timeout_s) == (CALL, "clinica-norte", FIND.timeout_s)
    assert asked.use == a_use("find_slot")
    assert tools.read_backs == {}


async def test_a_confirm_tool_keeps_its_read_back_rendered_from_the_call_and_the_result() -> None:
    recording = Recording(tools={"book": ToolResult(call_id="", name="book", output={"ref": "A7"})})
    tools = a_tools(recording)
    await tools.ran(BOOK, a_use("book", at="10:15"))
    assert tools.read_backs == {"tu_1": "Le reservé el turno de las 10:15, referencia A7."}


async def test_a_tool_that_failed_is_a_tool_error_the_model_reads_and_no_read_back() -> None:
    recording = Recording(tools={"book": ToolResult(call_id="", name="book", error="agenda down")})
    tools = a_tools(recording)
    with pytest.raises(ToolError, match="agenda down"):
        await tools.ran(BOOK, a_use("book", at="10:15"))
    assert tools.read_backs == {}


async def test_a_platform_that_refuses_is_a_tool_that_did_not_answer() -> None:
    with pytest.raises(ToolError, match="no app is holding"):
        await a_tools(Recording()).ran(FIND, a_use("find_slot"))


def test_a_placeholder_nobody_filled_stays_visible_instead_of_vanishing() -> None:
    result = ToolResult(call_id="x", name="book", output=None)
    assert (
        rendered("Reservé {{ at }} para {{who.name}}", {"at": "10:15"}, result)
        == "Reservé 10:15 para {{who.name}}"
    )
    assert rendered("Ref {{result}}", {}, result) == "Ref "


# ── the callable livekit runs, in the order the caller hears it ─────────────────


class _Timeline:
    """What happened on the line, in order: the announcement, the round trip, the receipt."""

    def __init__(self) -> None:
        self.happened: list[str] = []


class _Heard:
    """A speech handle as say() returns one: awaiting it is the audio finishing."""

    def __init__(self, timeline: _Timeline, text: str) -> None:
        self._timeline = timeline
        self._text = text

    def __await__(self):
        async def played() -> None:
            self._timeline.happened.append(f"heard {self._text}")

        return played().__await__()


class _Session:
    def __init__(self, timeline: _Timeline) -> None:
        self._timeline = timeline

    def say(self, text: str) -> _Heard:
        self._timeline.happened.append(f"said {text}")
        return _Heard(self._timeline, text)


class _Named:
    def __init__(self, **fields: str) -> None:
        self.__dict__.update(fields)


class _RunContext:
    """livekit's RunContext, as far as the callable reads it."""

    def __init__(self, timeline: _Timeline) -> None:
        self._timeline = timeline
        self.function_call = _Named(call_id="tu_1")
        self.speech_handle = _Named(id="sp_1")
        self.session = _Session(timeline)

    async def wait_for_playout(self) -> None:
        self._timeline.happened.append("announcement played out")


class _Platform(Recording):
    """The recording platform, also writing the round trip onto the timeline."""

    def __init__(self, timeline: _Timeline, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._timeline = timeline

    @override
    async def tool(self, call: str, agent: str, wanted: ToolCall, timeout_s: float) -> ToolResult:
        self._timeline.happened.append(f"ran {wanted.name}")
        return await super().tool(call, agent, wanted, timeout_s)


async def test_the_tool_runs_after_its_announcement_and_returns_after_its_receipt() -> None:
    """The model says "voy a reservar" and calls book in one response, and livekit starts the tool
    under that very line. The callable waits for the words spoken before it; then the round trip;
    then the receipt, heard out BEFORE the tool returns, because the reply to the result is
    generated the instant it does and a receipt still playing is not yet in the history."""
    timeline = _Timeline()
    platform = _Platform(
        timeline, tools={"book": ToolResult(call_id="", name="book", output={"ref": "A7"})}
    )
    tools = a_tools(platform)
    _find, book = tools.declared_tools
    text = await book({"at": "10:15"}, _RunContext(timeline))  # pyright: ignore[reportCallIssue, reportUnknownVariableType]
    assert "A7" in text
    assert timeline.happened == [
        "announcement played out",
        "ran book",
        "said Le reservé el turno de las 10:15, referencia A7.",
        "heard Le reservé el turno de las 10:15, referencia A7.",
    ]
    assert tools.read_backs == {}


async def test_a_tool_with_no_receipt_still_waits_for_its_announcement() -> None:
    timeline = _Timeline()
    platform = _Platform(
        timeline, tools={"find_slot": ToolResult(call_id="", name="find_slot", output="10:15")}
    )
    find, _book = a_tools(platform).declared_tools
    assert await find({}, _RunContext(timeline)) == "10:15"  # pyright: ignore[reportCallIssue, reportUnknownVariableType]
    assert timeline.happened == ["announcement played out", "ran find_slot"]
