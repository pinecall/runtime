"""A tool on a text call: the gate before the app, and a tools.set that never re-declares one."""

import json
from datetime import date
from typing import Any

import pytest
from livekit.agents import llm as agents

from pinecall.log.logs import CallLog
from pinecall.log.store import MemoryStore
from pinecall.session.text.session import TextSession
from pinecall.types import AgentConfig, CallContext, Route, ToolSpec
from pinecall_protocol import defs
from pinecall_testkit.fake_llm import FakeLLM, Scripted, a_call

pytestmark = pytest.mark.unit

A_CALL = "call_the_one_this_test_runs"
AGENT = "clinica-norte"
# Short enough that a test can wait for the app that never answers, long enough not to flake.
FIND = ToolSpec(
    name="find_slot", description="Free slots", parameters={"type": "object"}, timeout_s=0.2
)
BOOK = ToolSpec(
    name="book", description="Book a slot", parameters={"type": "object"}, timeout_s=0.2
)


def a_session(store: MemoryStore, llm: FakeLLM) -> TextSession:
    """One web call to a clinic that declared two tools."""
    context = CallContext(
        call=A_CALL,
        channel="web",
        direction="inbound",
        caller="web_someone",
        route=Route(org="clinica", agent=AGENT, channel="web", number=None),
        today=date(2026, 9, 8),
    )
    config = AgentConfig(slug=AGENT, tools=(FIND, BOOK))
    return TextSession(context, config, CallLog(store, AGENT, A_CALL), llm)


def wanted(*names: str) -> list[defs.ToolSpec]:
    return [defs.ToolSpec(name=name, description="", parameters={}) for name in names]


async def test_a_tool_the_app_closed_is_refused_before_the_app_and_the_model_reads_why() -> None:
    store = MemoryStore()
    llm = FakeLLM(
        Scripted(chunks=("Voy.",), calls=(a_call("bk_1", "book", {"at": "10:15"}),)),
        Scripted(chunks=("No puedo.",)),
    )
    session = a_session(store, llm)
    await session.start()
    await session.set_tools(wanted("find_slot"))
    await session.hears("reservame el de las 10:15")
    # The history also holds the clock's own pair, seeded at start; the tool's output is the other.
    output = next(one for one in llm.asked[1].outputs if one.name == "book")
    assert (output.is_error, output.output) == (True, "book is not available now")
    written = [entry.type for entry in await store.since(A_CALL)]
    assert "tool.call" not in written, "a tool.call entry is what the app runs a tool on"
    refused = next(entry for entry in await store.since(A_CALL) if entry.type == "error")
    assert refused.data == {
        "code": "refused",
        "message": "book is not available now",
        "recoverable": True,
    }


async def test_a_tool_the_app_left_open_still_goes_out_to_the_app() -> None:
    store = MemoryStore()
    llm = FakeLLM(
        Scripted(chunks=("Miro.",), calls=(a_call("fs_1", "find_slot", {}),)),
        Scripted(chunks=("Nada.",)),
    )
    session = a_session(store, llm)
    await session.start()
    await session.set_tools(wanted("find_slot"))
    await session.hears("¿hay hora?")
    written = [entry.type for entry in await store.since(A_CALL)]
    assert written.count("tool.call") == 1 and written.count("tool.result") == 1
    assert "error" not in written


async def test_a_tools_set_between_two_requests_leaves_the_providers_tools_byte_identical() -> None:
    """What the cache is for: tools come first in the prefix, and a re-declared one empties it."""
    llm = FakeLLM(Scripted(chunks=("Uno.",)), Scripted(chunks=("Dos.",)))
    session = a_session(MemoryStore(), llm)
    await session.start()
    await session.hears("hola")
    await session.set_tools(wanted("book"))
    await session.hears("reservame")
    first, second = llm.asked
    assert _the_tools_the_provider_reads(first) == _the_tools_the_provider_reads(second)
    assert first.tools == second.tools == ("find_slot", "book")


def _the_tools_the_provider_reads(asked: Any) -> str:
    """The tool schemas as the Anthropic plugin builds them (anthropic/llm.py:184), as bytes."""
    schemas: Any = agents.ToolContext(list(asked.declared)).parse_function_tools("anthropic")  # pyright: ignore[reportUnknownMemberType]
    return json.dumps(schemas, sort_keys=True)
