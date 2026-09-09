"""The prompt's regions: the view moves every turn and the cached prefix does not move at all."""

from __future__ import annotations

import json
from typing import Any, override

import pytest
from livekit.agents import llm as agents
from livekit.agents.voice import Agent
from livekit.agents.voice.generation import update_instructions

from pinecall.session.voice.regions import Regions
from tests.session.fake_llm import FakeLLM
from tests.session.voice.silence import anthropic_request

pytestmark = pytest.mark.unit

STATIC = "You are Clara, of Clínica Norte. Never invent an appointment."
A_VIEW = "The caller is Ana. Two slots are free."


class CountingAgent(Agent):
    """livekit's Agent, counting the two calls that cost a cache write."""

    def __init__(self) -> None:
        super().__init__(instructions=STATIC, tools=[], llm=FakeLLM())  # pyright: ignore[reportUnknownMemberType]
        self.rewrites = 0
        self.retools = 0

    @override
    async def update_instructions(self, instructions: str) -> None:
        self.rewrites += 1
        await super().update_instructions(instructions)

    @override
    async def update_tools(self, tools: list[agents.Tool | agents.Toolset]) -> None:
        self.retools += 1
        await super().update_tools(tools)


async def test_the_static_prefix_is_unchanged_after_a_tools_update() -> None:
    """The criterion, in the provider's own bytes: tools moved, the cached prefix did not."""
    context = _a_conversation()
    before = _the_cached_prefix(context)
    # 1.8 records a tools update as an AgentConfigUpdate item in the history
    # (agent_activity.py:631). It is a member of the ChatItem union and no formatter sends it.
    context.insert(agents.AgentConfigUpdate(tools_added=["book_slot"], tools_removed=["find_slot"]))
    assert _the_cached_prefix(context) == before


async def test_a_config_update_is_in_the_history_and_never_in_the_request() -> None:
    """What state.py must expect of 1.8: the item is recorded, and the model never reads it."""
    context = _a_conversation()
    context.insert(agents.AgentConfigUpdate(tools_added=["book_slot"]))
    said = json.dumps(anthropic_request(context)[0])
    assert any(isinstance(item, agents.AgentConfigUpdate) for item in context.items)
    assert "book_slot" not in said


async def test_a_tools_update_leaves_the_agents_instructions_where_they_were() -> None:
    agent = CountingAgent()
    regions = Regions(agent, STATIC)
    await regions.set_tools([])
    assert (agent.instructions, agent.rewrites, agent.retools) == (STATIC, 0, 1)


async def test_the_view_moves_without_the_prefix_being_rewritten() -> None:
    agent = CountingAgent()
    regions = Regions(agent, STATIC)
    await regions.set_prompt("view", A_VIEW)
    await regions.set_prompt("view", "The caller is Ana. One slot is free.")
    assert regions.view.endswith("One slot is free.")
    assert (regions.static, agent.rewrites) == (STATIC, 0)


async def test_the_same_instructions_twice_is_not_a_rewrite() -> None:
    """Rewriting the prefix with the same bytes still pays a cache write, so it is not done."""
    agent = CountingAgent()
    regions = Regions(agent, STATIC)
    await regions.set_prompt("static", STATIC)
    await regions.set_prompt("static", "You are Clara. Say less.")
    assert (agent.rewrites, agent.instructions) == (1, "You are Clara. Say less.")
    assert regions.static == "You are Clara. Say less."


def _a_conversation() -> agents.ChatContext:
    """A context as livekit builds one: the pinned instructions at index 0, then two turns."""
    context = agents.ChatContext.empty()
    update_instructions(context, instructions=STATIC, add_if_missing=True)
    context.add_message(role="user", content="Hola, quiero una cita.")
    context.add_message(role="assistant", content="Claro. ¿Para qué día?")
    return context


def _the_cached_prefix(context: agents.ChatContext) -> str:
    """The system blocks Anthropic's cache breakpoint lands on, as bytes a test can compare."""
    system: Any = anthropic_request(context)[1].system_messages
    return json.dumps(system, sort_keys=True)
