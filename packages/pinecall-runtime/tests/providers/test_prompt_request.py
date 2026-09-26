"""The blocks as a provider reads them, and where a lookup's answer lands in the same request."""

from __future__ import annotations

import json
from typing import Any

import pytest
from livekit.agents import llm as agents
from livekit.agents.voice.generation import update_instructions

from pinecall.providers.prompt_request import SystemBlocks, request_context
from pinecall.types import Blocks, PromptBlock
from pinecall_testkit.silent_kit import anthropic_request

pytestmark = pytest.mark.unit

IDENTITY = "You are Clara, of Clínica Norte. Never invent an appointment."
KNOWLEDGE = "The clinic opens at nine and closes at six."
TOOLS = "find_patient looks a patient up; book takes a slot."
A_VIEW = "The caller is Ana. Two slots are free."

A_FACT = "Prefiere que le llamen por la mañana."
A_CHUNK = "La revisión son cuarenta euros."
SAID = "¿Tiene algo el martes?"


def test_the_anthropic_request_carries_one_system_block_per_static_block_in_order() -> None:
    """What the cache is for: `system` arrives as [identity, knowledge, tools], in that order."""
    blocks = _written()
    request = request_context(_a_conversation(blocks), blocks)
    assert isinstance(request, SystemBlocks)
    assert _the_system_blocks(request) == [IDENTITY, KNOWLEDGE, TOOLS]


def test_rewriting_tools_leaves_the_first_two_blocks_byte_identical_and_moves_the_joined_text() -> (
    None
):
    blocks = _written()
    before = _the_system_blocks(request_context(_a_conversation(blocks), blocks))
    joined_before = blocks.instructions
    assert blocks.set("tools", "book takes a slot; cancel gives one back.") is True
    after = _the_system_blocks(request_context(_a_conversation(blocks), blocks))
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
    request = request_context(history, blocks)
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
    request_context(history, blocks, _a_lookup("recall", {"facts": [{"text": A_FACT}]}))
    request_context(history, blocks, _a_lookup("recall", {"facts": [{"text": A_FACT}]}))
    assert history.items == items_before


def test_a_block_nobody_wrote_sends_nothing_and_the_request_is_the_history_alone() -> None:
    blocks = Blocks()
    blocks.set("identity", IDENTITY)
    history = _a_conversation(blocks)
    request = request_context(history, blocks)
    assert request.items == history.items
    assert _the_system_blocks(request) == [IDENTITY]


def test_every_other_provider_reads_the_static_blocks_as_the_one_joined_string() -> None:
    """OpenAI caches by longest prefix on its own; the order of the blocks is the whole trick."""
    blocks = _written()
    request = request_context(_a_conversation(blocks), blocks)
    messages: Any = request.to_provider_format("openai")[0]  # pyright: ignore[reportUnknownMemberType]
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == f"{IDENTITY}\n\n{KNOWLEDGE}\n\n{TOOLS}"


# ── where a lookup's answer lands, which is the whole of docs/security/prompt-injection.md ──


def test_both_halves_of_a_fabricated_pair_survive_the_formatter_in_order() -> None:
    """A pair the formatter cannot group by call_id is a pair it drops: the model would see a
    tool_use with no result. This reads the request Anthropic would actually be sent."""
    blocks = _written()
    blocks.set("view", A_VIEW)
    messages, _data = anthropic_request(
        request_context(_the_caller_just_spoke(blocks), blocks, _two_lookups())
    )
    kinds = [
        (message["role"], block["type"], block.get("name") or block.get("tool_use_id"))
        for message in messages
        for block in message["content"]
    ]
    assert kinds[-6:] == [
        ("assistant", "tool_use", "recall"),
        ("user", "tool_result", "lu_1_recall"),
        ("assistant", "tool_use", "search"),
        ("user", "tool_result", "lu_2_search"),
        ("user", "text", None),
        ("user", "text", None),
    ]


# Where the pair goes, and the reason the whole file exists in this order. livekit hands
# `on_user_turn_completed` a context WITHOUT the caller's new message and appends it after
# (agent_activity.py:2599-2606, :2672), so anything a lookup adds there lands ahead of what they
# just said. Appended after it instead, their sentence sits several messages back from where the
# model decides, behind two tool_results — 0 of 8 on clinica-norte's own recorded request, 8 of 8
# with the words next to the view (2026-09-11).
def test_a_lookup_lands_ahead_of_the_caller_so_their_words_stay_next_to_the_view() -> None:
    """The request ends with the caller's sentence and then the view, with nothing between them."""
    blocks = _written()
    blocks.set("view", A_VIEW)

    messages, _data = anthropic_request(
        request_context(_the_caller_just_spoke(blocks), blocks, _two_lookups())
    )

    assert [block["text"] for block in messages[-1]["content"] if block["type"] == "text"] == [
        SAID,
        f"<instructions>\n{A_VIEW}\n</instructions>",
    ]


def test_a_turn_the_caller_did_not_open_keeps_its_lookups_at_the_end() -> None:
    """A tool step, or `agent.reply`: no newest message to sit ahead of, so nothing is moved."""
    blocks = _written()
    blocks.set("view", A_VIEW)

    request = request_context(_a_conversation(blocks), blocks, _two_lookups())

    # …the agent's own last turn, then the two pairs, then the view. The order this always had.
    assert [type(item).__name__ for item in request.items][-6:] == [
        "ChatMessage",
        "FunctionCall",
        "FunctionCallOutput",
        "FunctionCall",
        "FunctionCallOutput",
        "ChatMessage",
    ]


def _two_lookups() -> tuple[agents.ChatItem, ...]:
    """One recall and one search, both with something in them, as a real turn carries them."""
    return (
        *_a_lookup("recall", {"facts": [{"text": A_FACT, "source": "call_8f4a2c"}]}),
        *_a_lookup("search", {"chunks": [{"path": "tarifas.md", "text": A_CHUNK}]}, at=2),
    )


def test_a_tool_results_content_parses_as_json_and_its_key_is_facts_or_chunks() -> None:
    """JSON is the encoding, not prose: unambiguous delimiters an attacker cannot close."""
    blocks = _written()
    lookups = (
        *_a_lookup("recall", {"facts": [{"text": A_FACT, "source": "call_8f4a2c"}]}),
        *_a_lookup("search", {"chunks": [{"path": "tarifas.md", "text": A_CHUNK}]}, at=2),
    )
    messages, _data = anthropic_request(request_context(_a_conversation(blocks), blocks, lookups))
    results = [
        block
        for message in messages
        for block in message["content"]
        if block["type"] == "tool_result"
    ]
    read = [json.loads(result["content"]) for result in results]
    assert [list(one) for one in read] == [["facts"], ["chunks"]]
    assert read[0]["facts"][0] == {"text": A_FACT, "source": "call_8f4a2c"}


def test_no_fact_and_no_chunk_ever_reaches_the_system_field() -> None:
    """Nothing from outside the conversation carries operator authority. Ever."""
    blocks = _written()
    blocks.set("view", A_VIEW)
    lookups = (
        *_a_lookup("recall", {"facts": [{"text": A_FACT}]}),
        *_a_lookup("search", {"chunks": [{"text": A_CHUNK}]}, at=2),
    )
    request = request_context(_a_conversation(blocks), blocks, lookups)
    system = _the_system_blocks(request)
    assert system == [IDENTITY, KNOWLEDGE, TOOLS]
    for said in system:
        assert A_FACT not in said and A_CHUNK not in said


def test_the_view_is_never_placed_in_a_tool_result() -> None:
    """render() is the tenant's own words: a model is trained to discount a tool result."""
    blocks = _written()
    blocks.set("view", A_VIEW)
    lookups = _a_lookup("recall", {"facts": [{"text": A_FACT}]})
    messages, _data = anthropic_request(request_context(_a_conversation(blocks), blocks, lookups))
    results = [
        block
        for message in messages
        for block in message["content"]
        if block["type"] == "tool_result"
    ]
    assert all(A_VIEW not in json.dumps(result["content"]) for result in results)
    # It is the last thing in the request instead, wrapped as an instruction.
    last = messages[-1]["content"][-1]
    assert last["type"] == "text"
    assert last["text"] == f"<instructions>\n{A_VIEW}\n</instructions>"


def _a_lookup(
    tool: str, output: dict[str, Any], at: int = 1
) -> tuple[agents.FunctionCall, agents.FunctionCallOutput]:
    """One pair as session/lookup_tools.py builds it, with the ids that file gives them."""
    call_id = f"lu_{at}_{tool}"
    return (
        agents.FunctionCall(call_id=call_id, name=tool, arguments=json.dumps({"query": "hola"})),
        agents.FunctionCallOutput(
            call_id=call_id,
            name=tool,
            output=json.dumps(output, ensure_ascii=False),
            is_error=False,
            reply_required=False,
        ),
    )


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


# What `llm_node` is ACTUALLY handed: livekit appends the caller's new message before the reply is
# generated (agent_activity.py:2672), so the context always ends with what they just said. The
# fixture above ends on the agent instead, which is why it could not see where a lookup lands.
def _the_caller_just_spoke(blocks: Blocks, said: str = SAID) -> agents.ChatContext:
    """The same conversation one turn on, ending where every real request ends."""
    context = _a_conversation(blocks)
    context.add_message(role="user", content=said)
    return context


def _the_system_blocks(request: agents.ChatContext) -> list[str]:
    """The strings the plugin turns into `system` text blocks, in the order it sends them."""
    system: Any = anthropic_request(request)[1].system_messages
    return list(system)


def _a_message(item: agents.ChatItem) -> tuple[str, str]:
    """One history item as its role and text; a tool call here would be the test lying."""
    assert isinstance(item, agents.ChatMessage)
    return (item.role, item.text_content or "")


# The one block the platform writes and the app never does. The file a class ships with travels
# whole in the declaration, and until it was read into this block it reached nobody: a live call
# on 2026-09-10 sent identity, tools and the view, and the seventeen thousand characters of the
# clinic's own handbook went nowhere. docs/security/prompt-injection.md, the second row.
def test_the_file_a_class_ships_with_is_read_into_the_knowledge_block() -> None:
    blocks = Blocks(knowledge=A_CHUNK)
    assert blocks.text_of("knowledge") == A_CHUNK
    assert A_CHUNK in blocks.instructions


def test_a_class_that_ships_no_file_has_an_empty_knowledge_block() -> None:
    assert Blocks().text_of("knowledge") == ""


def test_the_knowledge_the_platform_wrote_reaches_the_model_as_its_own_system_block() -> None:
    blocks = Blocks(knowledge=A_CHUNK)
    blocks.set("identity", IDENTITY)
    blocks.set("tools", TOOLS)
    assert _the_system_blocks(request_context(_a_conversation(blocks), blocks)) == [
        IDENTITY,
        A_CHUNK,
        TOOLS,
    ]
