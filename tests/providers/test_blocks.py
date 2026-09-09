"""The blocks as a provider reads them: Anthropic one system block each, the rest one string."""

from __future__ import annotations

import json
from typing import Any

import pytest
from livekit.agents import llm as agents
from livekit.agents.voice.generation import update_instructions

from pinecall.providers.blocks import SystemBlocks, request_context
from pinecall.types import Blocks, PromptBlock
from tests.session.voice.silence import anthropic_request

pytestmark = pytest.mark.unit

IDENTITY = "You are Clara, of Clínica Norte. Never invent an appointment."
KNOWLEDGE = "The clinic opens at nine and closes at six."
TOOLS = "find_patient looks a patient up; book takes a slot."
A_VIEW = "The caller is Ana. Two slots are free."

A_KNOWLEDGE_MARKER = "<!-- knowledge: ./knowledge/clinica.md -->"
A_MEMORY_MARKER = '<!-- memory: {"limit":6} -->'
NOTHING: dict[str, str] = {}


def test_the_anthropic_request_carries_one_system_block_per_static_block_in_order() -> None:
    """What the cache is for: `system` arrives as [identity, knowledge, tools], in that order."""
    blocks = _written()
    request = request_context(_a_conversation(blocks), blocks, NOTHING)
    assert isinstance(request, SystemBlocks)
    assert _the_system_blocks(request) == [IDENTITY, KNOWLEDGE, TOOLS]


def test_rewriting_tools_leaves_the_first_two_blocks_byte_identical_and_moves_the_joined_text() -> (
    None
):
    blocks = _written()
    before = _the_system_blocks(request_context(_a_conversation(blocks), blocks, NOTHING))
    joined_before = blocks.instructions
    assert blocks.set("tools", "book takes a slot; cancel gives one back.") is True
    after = _the_system_blocks(request_context(_a_conversation(blocks), blocks, NOTHING))
    assert after[:2] == before[:2]
    assert after[2] != before[2]
    assert blocks.instructions != joined_before


def test_the_dynamic_blocks_land_after_the_history_in_layout_order_one_message_each() -> None:
    blocks = Blocks(
        (
            PromptBlock("identity", "static"),
            PromptBlock("availability", "dynamic"),
            PromptBlock("view", "dynamic"),
        )
    )
    blocks.set("identity", IDENTITY)
    blocks.set("view", A_VIEW)
    blocks.set("availability", "Free today: 10:15, 11:45.")
    history = _a_conversation(blocks)
    request = request_context(history, blocks, NOTHING)
    said = [_a_message(item) for item in request.items]
    assert [(role, text) for role, text in said[-2:]] == [
        ("system", "Free today: 10:15, 11:45."),
        ("system", A_VIEW),
    ]
    assert said[: len(history.items)] == [_a_message(item) for item in history.items]
    # And on the wire they close the request as one user turn of two instruction texts, in that
    # order, never as system: the formatter keeps only the first system item as the preamble.
    messages, _data = anthropic_request(request)
    assert messages[-1]["role"] == "user"
    texts = [block["text"] for block in messages[-1]["content"]]
    assert "Free today: 10:15, 11:45." in texts[0] and A_VIEW in texts[1]


def test_the_history_livekit_handed_in_is_left_exactly_as_it_was() -> None:
    """livekit reuses that copy across the tool steps of a turn: nothing of ours piles up in it."""
    blocks = _written()
    blocks.set("view", A_VIEW)
    history = _a_conversation(blocks)
    items_before = list(history.items)
    request_context(history, blocks, NOTHING)
    request_context(history, blocks, NOTHING)
    assert history.items == items_before


def test_a_block_nobody_wrote_sends_nothing_and_the_request_is_the_history_alone() -> None:
    blocks = Blocks()
    blocks.set("identity", IDENTITY)
    history = _a_conversation(blocks)
    request = request_context(history, blocks, NOTHING)
    assert request.items == history.items
    assert _the_system_blocks(request) == [IDENTITY]


def test_every_other_provider_reads_the_static_blocks_as_the_one_joined_string() -> None:
    """OpenAI caches by longest prefix on its own; the order of the blocks is the whole trick."""
    blocks = _written()
    request = request_context(_a_conversation(blocks), blocks, NOTHING)
    messages: Any = request.to_provider_format("openai")[0]  # pyright: ignore[reportUnknownMemberType]
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == f"{IDENTITY}\n\n{KNOWLEDGE}\n\n{TOOLS}"


async def test_a_knowledge_fill_in_a_static_block_is_the_same_bytes_on_every_request() -> None:
    """What the cache is for: the file's text is where the marker was, and it never moves."""
    blocks = _written()
    blocks.set("knowledge", f"## What you know\n\n{A_KNOWLEDGE_MARKER}")
    fills = {A_KNOWLEDGE_MARKER: KNOWLEDGE}
    first = _the_system_blocks(request_context(_a_conversation(blocks), blocks, fills))
    second = _the_system_blocks(request_context(_a_conversation(blocks), blocks, fills))
    assert first == second
    assert first[1] == f"## What you know\n\n{KNOWLEDGE}"
    assert A_KNOWLEDGE_MARKER not in json.dumps(
        anthropic_request(request_context(_a_conversation(blocks), blocks, fills))[0]
    )


async def test_a_memory_fill_in_the_dynamic_block_moves_between_turns_and_the_static_does_not() -> (
    None
):
    blocks = _written()
    blocks.set("view", f"{A_VIEW}\n\n## You remember\n\n{A_MEMORY_MARKER}")
    history = _a_conversation(blocks)
    first = request_context(history, blocks, {A_MEMORY_MARKER: "- Prefers mornings."})
    second = request_context(history, blocks, {A_MEMORY_MARKER: "- Prefers afternoons now."})
    assert _the_system_blocks(first) == _the_system_blocks(second)
    assert _a_message(first.items[-1])[1].endswith("- Prefers mornings.")
    assert _a_message(second.items[-1])[1].endswith("- Prefers afternoons now.")


async def test_the_apps_own_text_is_never_touched_by_a_fill() -> None:
    """prompt.changed hashes what the app wrote, markers and all; the fill lives in the request."""
    blocks = _written()
    written = f"## What you know\n\n{A_KNOWLEDGE_MARKER}"
    blocks.set("knowledge", written)
    request_context(_a_conversation(blocks), blocks, {A_KNOWLEDGE_MARKER: KNOWLEDGE})
    assert blocks.text_of("knowledge") == written
    assert blocks.instructions == f"{IDENTITY}\n\n{written}\n\n{TOOLS}"


async def test_every_other_provider_reads_the_filled_static_text_off_the_instructions_item() -> (
    None
):
    """OpenAI reads livekit's pinned item, so the fill has to be there too, under the same id."""
    blocks = _written()
    blocks.set("knowledge", A_KNOWLEDGE_MARKER)
    history = _a_conversation(blocks)
    request = request_context(history, blocks, {A_KNOWLEDGE_MARKER: KNOWLEDGE})
    messages: Any = request.to_provider_format("openai")[0]  # pyright: ignore[reportUnknownMemberType]
    assert messages[0]["content"] == f"{IDENTITY}\n\n{KNOWLEDGE}\n\n{TOOLS}"
    assert _a_message(history.items[0])[1] == blocks.instructions


async def test_a_marker_nobody_filled_reaches_no_provider() -> None:
    blocks = _written()
    blocks.set("knowledge", A_KNOWLEDGE_MARKER)
    blocks.set("view", A_MEMORY_MARKER)
    request = request_context(_a_conversation(blocks), blocks, NOTHING)
    assert "<!--" not in json.dumps(anthropic_request(request)[0])
    assert _the_system_blocks(request) == [IDENTITY, "", TOOLS]


def _written() -> Blocks:
    """The default layout with every static block written, as an app leaves it at call start."""
    blocks = Blocks()
    blocks.set("identity", IDENTITY)
    blocks.set("knowledge", KNOWLEDGE)
    blocks.set("tools", TOOLS)
    return blocks


def _a_conversation(blocks: Blocks) -> agents.ChatContext:
    """A context as livekit builds one: the pinned instructions at index 0, then two turns."""
    context = agents.ChatContext.empty()
    update_instructions(context, instructions=blocks.instructions, add_if_missing=True)
    context.add_message(role="user", content="Hola, quiero una cita.")
    context.add_message(role="assistant", content="Claro. ¿Para qué día?")
    return context


def _the_system_blocks(request: agents.ChatContext) -> list[str]:
    """The strings the plugin turns into `system` text blocks, in the order it sends them."""
    system: Any = anthropic_request(request)[1].system_messages
    return list(system)


def _a_message(item: agents.ChatItem) -> tuple[str, str]:
    """One history item as its role and text; a tool call here would be the test lying."""
    assert isinstance(item, agents.ChatMessage)
    return (item.role, item.text_content or "")
