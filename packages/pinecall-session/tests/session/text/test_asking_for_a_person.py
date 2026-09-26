"""A thread that asked for a person: the model falls quiet, and the ask ends one of two ways."""

from __future__ import annotations

import asyncio

import pytest

from pinecall.log.store import MemoryStore
from pinecall.session.text import supervise
from pinecall.session.text.session import TextSession
from pinecall_protocol import ProtocolError, verbs
from pinecall_protocol.commands import CallAttention, CallCallback, SupervisorVerb
from pinecall_protocol.defs import Supervisor
from pinecall_testkit.fake_llm import FakeLLM, Scripted
from tests.session.text.test_session import A_CALL, a_session

pytestmark = pytest.mark.unit

ANA = Supervisor(id="sup_ab12cd", name="Ana")
BECAUSE = "the contact is asking for a person"


async def a_thread() -> tuple[MemoryStore, TextSession]:
    """A started thread with a model that would answer anything it is allowed to answer."""
    store = MemoryStore()
    session = a_session(store, FakeLLM(Scripted(chunks=("Hola.",))))
    await session.start()
    return store, session


async def test_a_thread_waiting_for_a_person_logs_what_the_contact_writes_and_answers_none() -> (
    None
):
    store, session = await a_thread()
    await session.attending.asked(CallAttention(reason=BECAUSE, wait_s=30))
    await session.hears("¿hay alguien?")
    written = [entry.type for entry in await store.since(A_CALL)]
    assert "attention.requested" in written
    assert written.count("turn.user") == 1
    assert written.count("turn.agent") == 0  # the words are in the log and nobody answered them


async def test_a_supervisor_taking_the_thread_answers_the_ask() -> None:
    store, session = await a_thread()
    await session.attending.asked(CallAttention(reason=BECAUSE, wait_s=30))
    await supervise.apply_verb(
        session, SupervisorVerb(by=ANA, verb=verbs.TakeoverVerb(verb="takeover"))
    )
    (answered,) = [one for one in await store.since(A_CALL) if one.type == "attention.answered"]
    assert (answered.data["ok"], answered.data["by"]["id"]) == (True, ANA.id)
    assert not session.attending.open


async def test_a_wait_nobody_answered_gives_the_thread_back_to_the_model() -> None:
    store, session = await a_thread()
    await session.attending.asked(CallAttention(reason=BECAUSE, wait_s=0.01))
    await asyncio.sleep(0.05)
    (answered,) = [one for one in await store.since(A_CALL) if one.type == "attention.answered"]
    assert (answered.data["ok"], answered.data["by"]) == (False, None)
    await session.hears("¿hola?")
    written = [entry.type for entry in await store.since(A_CALL)]
    assert written.count("turn.agent") == 1


async def test_a_second_ask_while_the_contact_is_already_waiting_is_refused_by_name() -> None:
    _, session = await a_thread()
    await session.attending.asked(CallAttention(reason=BECAUSE, wait_s=30))
    with pytest.raises(ProtocolError, match="already waiting"):
        await session.attending.asked(CallAttention(reason=BECAUSE, wait_s=30))


async def test_a_callback_asked_for_on_a_thread_names_the_call_it_came_from() -> None:
    store, session = await a_thread()
    await session.call_back(CallCallback(number="+34600111222", when="mañana", note="una duda"))
    (asked,) = [one for one in await store.since(A_CALL) if one.type == "callback.requested"]
    assert (asked.data["number"], asked.data["via"]) == ("+34600111222", "agent")
    assert (asked.data["call"], asked.data["when"]) == (A_CALL, "mañana")
