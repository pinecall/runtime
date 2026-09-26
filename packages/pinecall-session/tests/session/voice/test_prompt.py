"""The prompt on a spoken call: the view moves every turn and the cached prefix does not move."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from livekit.agents import llm as agents
from livekit.agents.voice import AgentSession
from livekit.agents.voice.generation import update_instructions

from pinecall.session.voice import VoiceBridge, build_bridge
from pinecall.types import DeclarationRefused
from pinecall_testkit.fake_llm import FakeLLM, Scripted
from pinecall_testkit.fake_platform import CLARA, Recording
from pinecall_testkit.fake_platform import a_call as a_context
from pinecall_testkit.silent_kit import anthropic_request

pytestmark = pytest.mark.unit

STATIC = "You are Clara, of Clínica Norte. Never invent an appointment."
A_VIEW = "The caller is Ana. Two slots are free."

type Talking = tuple[Recording, VoiceBridge, AgentSession[None], FakeLLM]


@pytest.fixture
async def talking() -> AsyncIterator[Talking]:
    """A headless session on the scripted model, the bridge opened on it, the call started."""
    recording = Recording()
    llm = FakeLLM(Scripted(chunks=("Uno.",)), Scripted(chunks=("Dos.",)))
    bridge = build_bridge(a_context(), CLARA, recording)
    live: AgentSession[None] = AgentSession(
        llm=llm, vad=None, turn_handling={"turn_detection": "manual"}
    )
    await bridge.opened(live)
    await live.start(bridge.agent, record=False)  # pyright: ignore[reportUnknownMemberType]
    yield recording, bridge, live, llm
    await live.aclose()


async def test_the_static_prefix_is_unchanged_after_a_tools_update() -> None:
    """The criterion, in the provider's own bytes: tools moved, the cached prefix did not."""
    context = _a_conversation()
    before = _the_cached_prefix(context)
    # 1.8 records a tools update as an AgentConfigUpdate item in the history
    # (agent_activity.py:631). It is a member of the ChatItem union and no formatter sends it.
    context.insert(agents.AgentConfigUpdate(tools_added=["book_slot"], tools_removed=["find_slot"]))
    assert _the_cached_prefix(context) == before


async def test_a_config_update_is_in_the_history_and_never_in_the_request() -> None:
    """What the bridge must expect of 1.8: the item is recorded, and the model never reads it."""
    context = _a_conversation()
    context.insert(agents.AgentConfigUpdate(tools_added=["book_slot"]))
    said = json.dumps(anthropic_request(context)[0])
    assert any(isinstance(item, agents.AgentConfigUpdate) for item in context.items)
    assert "book_slot" not in said


async def test_the_view_reaches_the_model_last_and_never_touches_the_instructions(
    talking: Talking,
) -> None:
    """The seam a spoken call and a text call share: the request is built by request_context."""
    recording, bridge, live, llm = talking
    await bridge.set_prompt("identity", STATIC)
    await bridge.set_prompt("view", A_VIEW)
    await live.generate_reply(user_input="hola")
    await bridge.set_prompt("view", "The caller is Ana. One slot is free.")
    await live.generate_reply(user_input="¿y ahora?")
    first, second = llm.asked
    assert first.instructions == second.instructions == STATIC
    assert first.system.endswith(A_VIEW)
    assert second.system.endswith("One slot is free.")
    assert isinstance(second.chat_ctx.items[-1], agents.ChatMessage)
    assert [entry.data["name"] for entry in recording.of("prompt.changed")] == [
        "identity",
        "view",
        "view",
    ]


async def test_the_same_instructions_twice_is_not_a_rewrite(talking: Talking) -> None:
    """Rewriting the prefix with the same bytes still pays a cache write, so it is not done."""
    _recording, bridge, _live, _llm = talking
    # The activity records the configuration it started with as one such item (:1155); what a
    # prompt.set adds comes after it.
    at_start = len(_the_rewrites_of(bridge))
    await bridge.set_prompt("identity", STATIC)
    await bridge.set_prompt("identity", STATIC)
    await bridge.set_prompt("view", A_VIEW)
    assert _the_rewrites_of(bridge)[at_start:] == [STATIC]
    await bridge.set_prompt("identity", "You are Clara. Say less.")
    assert _the_rewrites_of(bridge)[at_start:] == [STATIC, "You are Clara. Say less."]
    assert bridge.agent.instructions == "You are Clara. Say less."


async def test_a_block_the_agent_never_declared_is_refused_and_leaves_no_entry(
    talking: Talking,
) -> None:
    recording, bridge, _live, _llm = talking
    with pytest.raises(DeclarationRefused, match="'faq'"):
        await bridge.set_prompt("faq", "We open at nine.")
    assert recording.of("prompt.changed") == []


def _the_rewrites_of(bridge: VoiceBridge) -> list[str]:
    """Every instructions rewrite livekit recorded in the history (agent_activity.py:601)."""
    return [
        str(item.instructions)
        for item in bridge.agent.chat_ctx.items
        if isinstance(item, agents.AgentConfigUpdate) and item.instructions is not None
    ]


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
