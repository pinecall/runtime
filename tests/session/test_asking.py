"""The requests of a call kept verbatim: what a broken golden is reproduced from."""

from typing import Any

import pytest
from livekit.agents import llm as agents
from livekit.agents.voice.generation import update_instructions

from pinecall.providers.prompt_request import request_context
from pinecall.session.asking import NotAsking, WhatWasAsked
from pinecall.types import Blocks, PromptBlock
from tests.session.voice.silence import anthropic_request

pytestmark = pytest.mark.unit

IDENTITY = "Eres la recepción de Clínica Norte."
TOOLS = "freeSlots: las horas libres de un día."
A_VIEW = "Hablas con Ana García, ya en la ficha."
A_SECOND_VIEW = "Le estás proponiendo el martes a las cuatro."
SAID = "¿Tiene algo el martes?"


@agents.function_tool
async def free_slots(day: str) -> str:
    """Horas libres de un día."""
    return day


def _written(view: str) -> Blocks:
    """The three regions of a real prompt: two static blocks, and the view that moves."""
    blocks = Blocks(
        (
            PromptBlock("identity", "static"),
            PromptBlock("tools", "static"),
            PromptBlock("view", "dynamic"),
        )
    )
    blocks.set("identity", IDENTITY)
    blocks.set("tools", TOOLS)
    blocks.set("view", view)
    return blocks


def _a_request(view: str = A_VIEW) -> Any:
    """One turn's request, built by the very function both agents build theirs with."""
    blocks = _written(view)
    history = agents.ChatContext.empty()
    # livekit's own instructions item, added the way the session adds it: it is the FIRST system
    # item, and without it the anthropic formatter reads the view as the preamble instead.
    update_instructions(history, instructions=blocks.instructions, add_if_missing=True)
    history.add_message(role="user", content=SAID)
    return request_context(history, blocks)


def test_a_live_call_keeps_nothing_and_runs_no_formatter() -> None:
    """The default costs a call nothing: `prompt.changed` carries a hash for exactly this reason."""
    assert NotAsking().asked(_a_request(), [free_slots], "anthropic") is None


def test_the_static_blocks_are_kept_apart_the_way_the_provider_receives_them() -> None:
    """A person reproducing a break reads the cached prefix as its own strings, in order."""
    asked = WhatWasAsked()

    asked.asked(_a_request(), [], "anthropic")

    assert asked.turns[0]["system"] == [IDENTITY, TOOLS]


def test_the_view_is_kept_where_the_model_read_it_after_the_caller() -> None:
    """The order IS the finding on a broken golden: the dynamic region is the last thing read,
    and the plugin wraps it in <instructions> inside the caller's own turn."""
    asked = WhatWasAsked()

    asked.asked(_a_request(), [], "anthropic")

    assert _texts(asked.turns[0]["messages"]) == [
        SAID,
        f"<instructions>\n{A_VIEW}\n</instructions>",
    ]


def test_every_request_is_kept_in_the_order_it_went_out() -> None:
    """A turn that runs a tool asks more than once: the last request is the one that answered."""
    asked = WhatWasAsked()

    asked.asked(_a_request(), [], "anthropic")
    asked.asked(_a_request(A_SECOND_VIEW), [], "anthropic")

    assert [_texts(turn["messages"])[-1] for turn in asked.turns] == [
        f"<instructions>\n{A_VIEW}\n</instructions>",
        f"<instructions>\n{A_SECOND_VIEW}\n</instructions>",
    ]


def test_the_tools_the_model_was_handed_are_kept_beside_the_words() -> None:
    """A call that ran no tool is judged on the list it actually had: it is part of the prompt."""
    asked = WhatWasAsked()

    asked.asked(_a_request(), [free_slots], "anthropic")

    assert [tool["function"]["name"] for tool in asked.turns[0]["tools"]] == ["free_slots"]


# Every tenant tool is a raw-schema tool: the app runs the body, so there is no Python function
# to introspect (session/declaring.py). A reader that only knew livekit's decorated kind wrote
# `tools: []` under the very calls whose finding was that they ran no tool at all.
def test_a_tenant_tool_is_kept_too_and_it_is_the_raw_kind() -> None:
    """The list must be the whole list, or the one number a broken golden turns on is a lie."""
    asked = WhatWasAsked()

    asked.asked(_a_request(), [_a_tenant_tool(), free_slots], "anthropic")

    assert [_named(tool) for tool in asked.turns[0]["tools"]] == ["freeSlots", "free_slots"]


def test_what_it_keeps_is_what_the_provider_would_have_been_sent() -> None:
    """Not a summary of the request: the formatter's own output, so nothing is lost on the way."""
    request = _a_request()
    asked = WhatWasAsked()

    asked.asked(request, [], "anthropic")

    messages, extra = anthropic_request(request)
    assert asked.turns[0] == {
        "system": list(extra.system_messages),
        "messages": messages,
        "tools": [],
    }


def _a_tenant_tool() -> Any:
    """One tool as `declared()` builds it: a schema the app owns, and a callable of ours."""

    async def call(raw_arguments: dict[str, Any], context: Any) -> str:  # noqa: ARG001
        return ""

    return agents.function_tool(
        call,
        raw_schema={
            "name": "freeSlots",
            "description": "Horas libres de un día.",
            "parameters": {"type": "object", "properties": {"day": {"type": "string"}}},
        },
    )


def _named(tool: Any) -> str:
    """A tool's name, whichever of the two shapes the request carried it in."""
    return tool["function"]["name"] if "function" in tool else tool["name"]


def _texts(messages: Any) -> list[str]:
    """The words of each message, however the formatter wrapped them."""
    said: list[str] = []
    for message in messages:
        said.extend(part["text"] for part in message["content"] if part.get("type") == "text")
    return said
