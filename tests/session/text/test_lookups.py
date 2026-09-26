"""The lookups of a text call: run on the caller's words, remembered at hang-up, same entries."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

import pytest

from pinecall._settings import Budgets
from pinecall.log.logs import CallLog
from pinecall.log.store import MemoryStore
from pinecall.session.text.session import TextSession
from pinecall.types import AgentConfig, CallContext, Docs, MemoryPolicy, PlatformTool, Route
from tests.session.fake_llm import FakeLLM, Scripted
from tests.session.text.test_session import A_CALL, A_TUESDAY, AGENT

pytestmark = pytest.mark.unit

A_VIEW = "The caller is Ana. Two slots are free."

REMEMBERS = AgentConfig(
    slug=AGENT,
    memory=MemoryPolicy(remember=("preference",)),
    bases=(Docs(base="clinica", k=4),),
)


class Answering:
    """A lookup service that answers both tools, and a rememberer that may fail."""

    def __init__(self, after_s: float = 0.0, failing: Exception | None = None) -> None:
        self._after_s = after_s
        self._failing = failing
        self.asked: list[tuple[str, Mapping[str, Any], str | None]] = []
        self.remembered: list[str] = []

    async def lookup(
        self, call: str, tool: PlatformTool, input: Mapping[str, Any], speech_id: str | None
    ) -> Mapping[str, Any]:
        assert call == A_CALL
        self.asked.append((tool, dict(input), speech_id))
        await asyncio.sleep(self._after_s)
        if tool == "recall":
            return {
                "facts": [{"text": "prefiere la mañana", "source": None, "since": "2026-09-01"}]
            }
        return {"chunks": [{"path": "tarifas.md", "heading": "Tarifas", "text": "Son 45 €."}]}

    async def remember(self, call: str) -> int:
        if self._failing is not None:
            raise self._failing
        self.remembered.append(call)
        return 1


def a_session(
    store: MemoryStore,
    llm: FakeLLM,
    answering: Answering,
    config: AgentConfig = REMEMBERS,
    budgets: Budgets = Budgets(),  # noqa: B008 — frozen
) -> TextSession:
    """One web call for the agent that remembers, looked up and remembered by `answering`."""
    context = CallContext(
        call=A_CALL,
        channel="web",
        direction="inbound",
        caller="web_someone",
        route=Route(org="clinica", agent=AGENT, channel="web", number=None),
        today=A_TUESDAY,
    )
    log = CallLog(store, AGENT, A_CALL)
    return TextSession(
        context, config, log, llm, lookup=answering, rememberer=answering, budgets=budgets
    )


async def test_the_callers_words_are_the_query_filed_under_their_own_speech() -> None:
    answering = Answering()
    llm = FakeLLM(Scripted(chunks=("Uno.",)))
    session = a_session(MemoryStore(), llm, answering)
    await session.start()
    await session.set_prompt("view", A_VIEW)
    await session.hears("quiero un turno")
    assert answering.asked == [
        ("recall", {"query": "quiero un turno"}, "sp_1"),
        ("search", {"query": "quiero un turno"}, "sp_1"),
    ]
    (asked,) = llm.asked
    # `current_date` is seeded at call start (session/date_tool.py); the lookups close the request.
    assert [call.name for call in asked.calls][-2:] == ["recall", "search"]
    assert [list(json.loads(output.output)) for output in asked.outputs][-2:] == [
        ["facts"],
        ["chunks"],
    ]
    assert asked.system.endswith(A_VIEW)


async def test_an_agent_reply_is_no_query_and_asks_nobody() -> None:
    """agent.reply puts the app's words in the history as the caller's; nothing is looked up."""
    answering = Answering()
    session = a_session(MemoryStore(), FakeLLM(), answering)
    await session.start()
    await session.set_prompt("view", A_VIEW)
    await session.reply("Offer the ten o'clock slot.")
    assert answering.asked == []


async def test_recall_and_search_are_declared_only_when_the_class_declares_them() -> None:
    llm = FakeLLM(Scripted(chunks=("Uno.",)))
    session = a_session(MemoryStore(), llm, Answering(), config=replace(REMEMBERS, bases=()))
    await session.start()
    await session.hears("hola")
    (asked,) = llm.asked
    assert "recall" in asked.tools
    assert "search" not in asked.tools


async def test_a_lookup_past_its_budget_is_a_recoverable_entry_and_the_turn_goes_on() -> None:
    store = MemoryStore()
    session = a_session(
        store,
        FakeLLM(),
        Answering(after_s=0.5),
        config=replace(REMEMBERS, bases=()),
        budgets=Budgets(text_lookup_ms=0, remember_s=1.0),
    )
    await session.start()
    await session.set_prompt("view", A_VIEW)
    await session.hears("hola")
    written = await store.since(A_CALL)
    (skipped,) = [entry for entry in written if entry.type == "error"]
    assert skipped.data["code"] == "recall_skipped"
    assert skipped.data["recoverable"] is True
    types = [entry.type for entry in written]
    assert types.index("turn.user") < types.index("error") < types.index("turn.agent")


async def test_hang_up_remembers_between_call_ended_and_call_summary() -> None:
    store = MemoryStore()
    answering = Answering()
    session = a_session(store, FakeLLM(), answering)
    await session.start()
    await session.hears("hola")
    await session.hangup("caller_hung_up", "caller")
    assert answering.remembered == [A_CALL]
    types = [entry.type for entry in await store.since(A_CALL)]
    assert types[-3:] == ["call.ended", "call.summary", "call.score"]


async def test_a_rememberer_that_fails_is_an_entry_and_the_call_still_seals() -> None:
    store = MemoryStore()
    session = a_session(store, FakeLLM(), Answering(failing=RuntimeError("no model at hang-up")))
    await session.start()
    await session.hangup("caller_hung_up", "caller")
    written = await store.since(A_CALL)
    assert [entry.type for entry in written][-4:] == [
        "call.ended",
        "error",
        "call.summary",
        "call.score",
    ]
    assert written[-3].data == {
        "code": "remember_failed",
        "message": "memory was not written: no model at hang-up",
        "recoverable": True,
    }


async def test_an_agent_that_declared_no_memory_asks_nobody_at_hang_up() -> None:
    answering = Answering()
    session = a_session(MemoryStore(), FakeLLM(), answering, config=replace(REMEMBERS, memory=None))
    await session.start()
    await session.hangup("caller_hung_up", "caller")
    assert answering.remembered == []
