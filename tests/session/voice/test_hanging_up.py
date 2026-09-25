"""The model's own end of a call: livekit's tool, and the entry our log would otherwise have got."""

from __future__ import annotations

from typing import Any, cast

import pytest
from livekit.agents import llm as agents
from livekit.agents.beta.tools import EndCallTool
from livekit.agents.llm import FunctionTool, ToolFlag
from livekit.agents.llm.tool_context import FunctionToolInfo, StopResponse
from livekit.agents.voice.events import CloseReason

from pinecall.session.voice.hanging_up import (
    HOW_IT_ENDED,
    SAY_GOODBYE_FIRST,
    a_way_to_hang_up,
    silence_after,
    the_reason_first,
)
from pinecall.types import AgentConfig, Hangup

pytestmark = pytest.mark.unit

A_CLINIC = "clinica-norte"


def what_the_model_reads(given: list[agents.Toolset]) -> FunctionToolInfo:
    """The one function a toolset carries, as the model is told about it: name, words and flags."""
    # livekit types a toolset's `tools` as either kind and only one of them carries `info`, so a
    # reader that wants the description the model is sent has to say which kind it is holding.
    one = cast(FunctionTool[..., Any], given[0].tools[0])
    return one.info


class Wrote:
    """A bridge that remembers being told the model ended the call, and what it logged for it."""

    def __init__(self) -> None:
        self.ended = False
        self.entries: list[tuple[str, Any]] = []

    def ended_by_the_model(self) -> None:
        self.ended = True

    async def a_platform_tool_ran(self, called: Any, result: Any) -> None:
        self.entries.append(("tool.call", called))
        self.entries.append(("tool.result", result))


class AnEndCall:
    """The event livekit hands on_tool_called: the run context, with the call and the speech."""

    class _Ctx:
        class function_call:
            call_id = "fc_1"

        class speech_handle:
            id = "speech_9"

    ctx = _Ctx()


def test_a_class_that_declares_no_hangup_gets_no_tool() -> None:
    assert a_way_to_hang_up(AgentConfig(slug=A_CLINIC), Wrote()) == []


def test_a_class_that_declares_one_gets_livekits_own_end_call() -> None:
    given = a_way_to_hang_up(AgentConfig(slug=A_CLINIC, hangup=Hangup()), Wrote())

    assert len(given) == 1
    assert isinstance(given[0], EndCallTool)
    assert what_the_model_reads(given).name == "end_call"


def test_the_tenants_own_words_reach_the_description_the_model_reads() -> None:
    said = "cuando el paciente ya tiene su cita y se despide"
    given = a_way_to_hang_up(AgentConfig(slug=A_CLINIC, hangup=Hangup(when=said)), Wrote())

    assert said in (what_the_model_reads(given).description or "")


def test_the_model_is_told_to_say_goodbye_in_the_turn_it_hangs_up_in() -> None:
    given = a_way_to_hang_up(AgentConfig(slug=A_CLINIC, hangup=Hangup()), Wrote())

    assert SAY_GOODBYE_FIRST in (what_the_model_reads(given).description or "")


async def test_nothing_is_generated_after_end_call() -> None:
    """livekit's own tool would answer "say goodbye to the user" and let the model speak once
    more to a caller already wished goodbye; ours asks for silence, livekit's own way."""
    with pytest.raises(StopResponse):
        await silence_after(None)  # pyright: ignore[reportArgumentType]


def test_the_tool_is_hidden_while_the_agent_is_greeting() -> None:
    given = a_way_to_hang_up(AgentConfig(slug=A_CLINIC, hangup=Hangup()), Wrote())

    assert what_the_model_reads(given).flags & ToolFlag.IGNORE_ON_ENTER


async def test_the_reason_is_written_down_before_livekit_closes_the_session() -> None:
    bridge = Wrote()
    a_way_to_hang_up(AgentConfig(slug=A_CLINIC, hangup=Hangup()), bridge)

    await the_reason_first(bridge)(AnEndCall())  # pyright: ignore[reportArgumentType]

    assert bridge.ended is True


async def test_end_call_is_in_the_log_like_any_other_tool() -> None:
    """A log without it showed the agent speaking twice in a row with nothing in between."""
    bridge = Wrote()

    await the_reason_first(bridge)(AnEndCall())  # pyright: ignore[reportArgumentType]

    assert [kind for kind, _ in bridge.entries] == ["tool.call", "tool.result"]
    called = bridge.entries[0][1]
    assert (called.name, called.call_id, called.speech_id) == ("end_call", "fc_1", "speech_9")


# The whole reason the callback exists: livekit ends the call with `session.shutdown()`, which
# closes as USER_INITIATED, and that is the entry a deploy taking the worker down writes. Left to
# the map alone, a caller saying "that's all, bye" would read as `drained` by the `platform`.
def test_the_close_reason_alone_would_have_called_it_a_drain() -> None:
    assert HOW_IT_ENDED[CloseReason.USER_INITIATED] == ("drained", "platform")
