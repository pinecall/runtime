"""The session on its own: what it opens with, and how a call ends when the caller leaves."""

import json
from datetime import date

import pytest
from livekit.agents import llm as agents

from pinecall.log.logs import CallLog
from pinecall.log.store import LogSealed, MemoryStore
from pinecall.session import clock
from pinecall.session.text.session import TextSession
from pinecall.types import AgentConfig, CallContext, Route
from tests.session.fake_llm import FakeLLM, Scripted

pytestmark = pytest.mark.unit

A_CALL = "call_the_one_this_test_runs"
AGENT = "clinica-norte"
A_TUESDAY = date(2026, 9, 8)


def a_session(store: MemoryStore, llm: FakeLLM, today: date = A_TUESDAY) -> TextSession:
    """One web call for an agent with nothing declared but its name."""
    context = CallContext(
        call=A_CALL,
        channel="web",
        direction="inbound",
        caller="web_someone",
        route=Route(org="clinica", agent=AGENT, channel="web", number=None),
        today=today,
    )
    config = AgentConfig(slug=AGENT, channels=frozenset({"web"}), instructions="Sos Clara.")
    return TextSession(context, config, CallLog(store, AGENT, A_CALL), llm)


async def test_a_caller_who_leaves_ends_the_call_and_the_verdict_closes_the_log() -> None:
    store = MemoryStore()
    session = a_session(store, FakeLLM(Scripted(chunks=("Hola.",))))
    await session.start()
    await session.hears("hola")
    await session.hangup("caller_hung_up", "caller")
    written = await store.since(A_CALL)
    ended = ["call.ended", "call.summary", "call.score"]
    assert [entry.type for entry in written][-3:] == ended
    assert written[-3].data["reason"] == "caller_hung_up"
    assert written[-3].data["ended_by"] == "caller"
    assert written[-2].data["outcome"] == "Hola."


async def test_nothing_can_be_appended_to_a_call_that_is_over() -> None:
    store = MemoryStore()
    session = a_session(store, FakeLLM())
    await session.start()
    await session.hangup("caller_hung_up", "caller")
    with pytest.raises(LogSealed):
        await session.say("one more thing")


async def test_a_hangup_twice_is_a_hangup_once() -> None:
    """A caller's socket can drop while the app is hanging up; the log says it happened once."""
    store = MemoryStore()
    session = a_session(store, FakeLLM())
    await session.start()
    await session.hangup("agent_hung_up", "agent")
    await session.hangup("caller_hung_up", "caller")
    written = await store.since(A_CALL)
    assert [entry.type for entry in written].count("call.ended") == 1


async def test_a_text_call_is_told_what_day_it_is_before_the_caller_says_a_word() -> None:
    """The pair a voice call opens with: a caller who writes "mañana" needs a calendar too."""
    session = a_session(MemoryStore(), FakeLLM())

    await session.start()

    assert _the_dates_in(session) == [{"today": "2026-09-08", "weekday": "tuesday"}]
    assert _the_turns_in(session) == []


async def test_the_date_is_seeded_once_a_call_and_never_once_a_turn() -> None:
    """A pair per turn would be a history that says today three times and costs tokens for it."""
    session = a_session(MemoryStore(), FakeLLM(Scripted(chunks=("Hola.",))))

    await session.start()
    await session.hears("hola")
    await session.hears("¿tiene hora el martes?")

    assert _the_dates_in(session) == [{"today": "2026-09-08", "weekday": "tuesday"}]


def _the_turns_in(session: TextSession) -> list[str]:
    """Who has spoken so far: the prompt is livekit's own system message and nobody's turn."""
    return [
        message.role
        for message in session.text_agent.chat_ctx.messages()
        if message.role != "system"
    ]


def _the_dates_in(session: TextSession) -> list[dict[str, str]]:
    """Every date the model's history holds, read as the JSON the model reads."""
    return [
        json.loads(item.output)
        for item in session.text_agent.chat_ctx.items
        if isinstance(item, agents.FunctionCallOutput) and item.name == clock.CLOCK_TOOL
    ]
