"""A WhatsApp conversation outlives the gateway: after a restart the next message goes on it."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from pinecall.api.agents.registry import Registry
from pinecall.api.app import app
from pinecall.api.live import Live
from pinecall.api.whatsapp import threads as whatsapp_threads
from pinecall.api.whatsapp.threads import Threads
from pinecall.log.store import MemoryStore
from pinecall.log.writers import Logs
from pinecall.routes.records import MemoryRoutes
from tests.api.conftest import AGENT
from tests.api.whatsapp.conftest import (
    AN_INSTANT,
    ANA,
    NEVER_IN_THIS_SUITE,
    THE_CLINICS_NUMBER,
    a_body,
    a_text,
    delivered,
    quiet,
    the_clinic_answers_at_the_number,
)
from tests.session.fake_llm import FakeLLM, Scripted

pytestmark = pytest.mark.unit

HOLA = "Hola, ¿tienen algo el martes?"
AN_ANSWER = "Sí, el martes a las 10:15."
ANOTHER_ANSWER = "Te la reservo."


async def restarted(threads: Threads, live: Live, logs: Logs, idle_seconds: float) -> Threads:
    """What a restart leaves: a process with no conversation open, and the logs whole."""
    thread = await quiet(threads)
    call = thread.session.call
    live.close(call)
    logs.forget(call)
    fresh = Threads(idle_seconds=idle_seconds)
    app.dependency_overrides[whatsapp_threads.the_threads] = lambda: fresh
    return fresh


async def test_the_next_message_after_a_restart_goes_on_the_same_call_with_its_history(
    meta: httpx.AsyncClient,
    registry: Registry,
    routes: MemoryRoutes,
    threads: Threads,
    live: Live,
    logs: Logs,
    store: MemoryStore,
    llm: FakeLLM,
) -> None:
    llm.script.extend((Scripted(chunks=(AN_ANSWER,)), Scripted(chunks=(ANOTHER_ANSWER,))))
    await the_clinic_answers_at_the_number(registry, routes)
    await delivered(meta, a_body(a_text(HOLA)))
    first = (await quiet(threads)).session.call

    fresh = await restarted(threads, live, logs, NEVER_IN_THIS_SUITE)
    await delivered(meta, a_body(a_text("dale", id="wamid.two")))
    again = await quiet(fresh)

    assert again.session.call == first
    assert await store.list_calls(AGENT) == [first]
    heard = [message.text_content for message in llm.asked[-1].history]
    assert heard[:3] == [HOLA, AN_ANSWER, "dale"]


async def test_a_conversation_that_went_quiet_while_nobody_watched_ends_and_a_new_one_opens(
    meta: httpx.AsyncClient,
    registry: Registry,
    routes: MemoryRoutes,
    threads: Threads,
    live: Live,
    logs: Logs,
    store: MemoryStore,
    llm: FakeLLM,
) -> None:
    llm.script.extend((Scripted(chunks=(AN_ANSWER,)), Scripted(chunks=(ANOTHER_ANSWER,))))
    await the_clinic_answers_at_the_number(registry, routes)
    await delivered(meta, a_body(a_text(HOLA)))
    first = (await quiet(threads)).session.call

    fresh = await restarted(threads, live, logs, AN_INSTANT)
    await asyncio.sleep(AN_INSTANT * 3)  # longer than the idle period, with nobody watching
    await delivered(meta, a_body(a_text("dale", id="wamid.two")))
    again = fresh.of(THE_CLINICS_NUMBER, ANA)

    assert again is not None and again.session.call != first
    ended = [entry.type for entry in await store.since(first, after=0)]
    assert ended[-3:] == ["call.ended", "call.summary", "call.score"]
