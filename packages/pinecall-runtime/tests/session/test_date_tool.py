"""Today's date: the model reads it as a tool it called, never as something the caller said."""

from __future__ import annotations

import json
from datetime import date
from typing import Any, cast

import pytest
from livekit.agents import llm as agents
from livekit.agents.voice import Agent

from pinecall.session import date_tool
from pinecall_testkit.fake_llm import FakeLLM
from pinecall_testkit.silent_kit import anthropic_request

pytestmark = pytest.mark.unit

A_SUNDAY = date(2026, 9, 6)


def test_the_date_is_a_call_and_its_answer_under_one_id() -> None:
    call, output = date_tool.date_tool_pair(A_SUNDAY)
    assert (call.name, call.call_id, call.arguments) == (date_tool.CLOCK_TOOL, output.call_id, "{}")
    assert json.loads(output.output) == {"today": "2026-09-06", "weekday": "sunday"}
    assert output.is_error is False


def test_the_answer_asks_for_no_reply() -> None:
    """Nothing was asked: a realtime model must not start talking because it read the date."""
    _call, output = date_tool.date_tool_pair(A_SUNDAY)
    assert output.reply_required is False


def test_the_weekday_does_not_depend_on_the_locale_of_the_process() -> None:
    week = [date_tool.date_tool_pair(date(2026, 9, 7 + day))[1].output for day in range(7)]
    weekdays = [json.loads(said)["weekday"] for said in week]
    assert weekdays == list(date_tool.WEEKDAYS)


def test_the_pair_reaches_the_provider_as_a_pair_and_never_as_an_instruction() -> None:
    """The whole reason for a pair: a system message appended here arrives as the caller talking."""
    context = agents.ChatContext.empty()
    context.items.extend(date_tool.date_tool_pair(A_SUNDAY))
    context.add_message(role="user", content="¿Qué día es hoy?")
    messages = anthropic_request(context)[0]
    kinds = [block["type"] for message in messages for block in _blocks(message)]
    assert "tool_use" in kinds and "tool_result" in kinds
    assert all(message["role"] != "system" for message in messages)


async def test_the_pair_is_in_the_history_before_the_first_turn() -> None:
    agent = Agent(instructions="You are Clara.", tools=[], llm=FakeLLM())  # pyright: ignore[reportUnknownMemberType]
    await date_tool.seed_date(agent, A_SUNDAY)
    items = agent.chat_ctx.items
    assert isinstance(items[0], agents.FunctionCall)
    assert isinstance(items[1], agents.FunctionCallOutput)
    assert "2026-09-06" in items[1].output


def _blocks(message: dict[str, Any]) -> list[dict[str, Any]]:
    """A provider message's content blocks; a plain string message has none to read."""
    content: Any = message.get("content")
    if not isinstance(content, list):
        return []
    blocks = cast("list[Any]", content)
    return [block for block in blocks if isinstance(block, dict)]
