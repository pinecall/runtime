"""The markers on a text call: filled on the caller's words, remembered at hang-up, same entries."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

import pytest

from pinecall._settings import Budgets
from pinecall.log import hashed_prompt
from pinecall.log.logs import CallLog
from pinecall.log.store import MemoryStore
from pinecall.session.text.session import TextSession
from pinecall.types import AgentConfig, CallContext, KnowledgeFile, Marker, MemoryPolicy, Route
from tests.session.fake_llm import FakeLLM, Scripted
from tests.session.text.test_session import A_CALL, A_TUESDAY, AGENT

pytestmark = pytest.mark.unit

A_FILE = KnowledgeFile("./knowledge/clinica.md", "The clinic opens at nine and closes at six.")
KNOWLEDGE = "<!-- knowledge: ./knowledge/clinica.md -->"
MEMORY = '<!-- memory: {"kinds":["preference"],"limit":6} -->'
A_VIEW = f"The caller is Ana.\n\n## You remember\n\n{MEMORY}"

REMEMBERS = AgentConfig(
    slug=AGENT,
    channels=frozenset({"web"}),
    knowledge=A_FILE,
    memory=MemoryPolicy(remember=("preference",)),
)


class Answering:
    """A filler that answers memory with what it was asked, and a rememberer that may fail."""

    def __init__(self, failing: Exception | None = None) -> None:
        self._failing = failing
        self.queries: list[tuple[str, str | None]] = []
        self.remembered: list[str] = []

    async def fill(
        self, call: str, query: str, markers: Sequence[Marker], speech_id: str | None
    ) -> Mapping[str, str]:
        assert call == A_CALL
        self.queries.append((query, speech_id))
        return {marker.line: f"- The caller said {query!r}." for marker in markers}

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
    """One web call for the agent that remembers, filled and remembered by `answering`."""
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
        context, config, log, llm, filler=answering, rememberer=answering, budgets=budgets
    )


async def test_the_callers_words_are_the_query_filed_under_their_speech_and_fill_the_view() -> None:
    answering = Answering()
    llm = FakeLLM(Scripted(chunks=("Uno.",)))
    session = a_session(MemoryStore(), llm, answering)
    await session.start()
    await session.set_prompt("view", A_VIEW)
    await session.hears("quiero un turno")
    assert answering.queries == [("quiero un turno", "sp_1")]
    (asked,) = llm.asked
    assert asked.system.endswith("## You remember\n\n- The caller said 'quiero un turno'.")
    assert MEMORY not in asked.system


async def test_an_agent_reply_is_no_query_and_asks_nobody() -> None:
    """agent.reply puts the app's words in the history as the caller's; memory is not searched."""
    answering = Answering()
    session = a_session(MemoryStore(), FakeLLM(), answering)
    await session.start()
    await session.set_prompt("view", A_VIEW)
    await session.reply("Offer the ten o'clock slot.")
    assert answering.queries == []


async def test_the_knowledge_file_is_in_the_static_prefix_and_the_hash_is_of_the_apps_text() -> (
    None
):
    store = MemoryStore()
    llm = FakeLLM(Scripted(chunks=("Uno.",)), Scripted(chunks=("Dos.",)))
    session = a_session(store, llm, Answering())
    await session.start()
    written = f"## What you know\n\n{KNOWLEDGE}"
    await session.set_prompt("knowledge", written)
    await session.hears("hola")
    await session.hears("¿a qué hora abren?")
    first, second = llm.asked
    assert first.instructions == second.instructions == f"## What you know\n\n{A_FILE.text}"
    assert session.text_agent.instructions == written
    (changed,) = [entry for entry in await store.since(A_CALL) if entry.type == "prompt.changed"]
    assert changed.data["hash"] == hashed_prompt(written)


async def test_a_fill_past_its_budget_is_a_recoverable_entry_and_the_turn_goes_on() -> None:
    store = MemoryStore()
    session = a_session(store, FakeLLM(), Answering(), budgets=Budgets(fill_ms=0, remember_s=1.0))
    await session.start()
    await session.set_prompt("view", A_VIEW)
    await session.hears("hola")
    written = await store.since(A_CALL)
    (skipped,) = [entry for entry in written if entry.type == "error"]
    assert skipped.data["code"] == "memory_skipped"
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
