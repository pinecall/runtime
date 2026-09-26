"""A WhatsApp message nobody could answer yet waits on the agent's log, then is answered."""

from __future__ import annotations

import time
from typing import Any

import httpx
import pytest

from pinecall.api.agents.registry import Registry
from pinecall.api.whatsapp.threads import WINDOW_SECONDS, Threads
from pinecall.api.whatsapp.unanswered import TAKEN, WAITING, Waiting, WaitingRoom
from pinecall.log.store import MemoryStore
from pinecall.log.writers import Logs
from pinecall.routes.records import MemoryRoutes
from pinecall.types import PRODUCTION
from pinecall.whatsapp.inbound import Inbound
from tests.api.conftest import A_RECORD, AGENT
from tests.api.whatsapp.conftest import (
    AN_APP,
    ANA,
    THE_CLINICS_NUMBER,
    a_body,
    a_text,
    delivered,
    quiet,
    the_operators_row,
)
from tests.session.fake_llm import FakeLLM, Scripted

pytestmark = pytest.mark.unit

HOLA = "Hola, ¿tienen algo el martes?"


async def written(store: MemoryStore, type: str) -> list[dict[str, Any]]:
    """The agent's own log, kept to one type."""
    return [entry.data for entry in await store.agent_since(AGENT, after=0) if entry.type == type]


async def test_a_message_nobody_can_answer_is_kept_and_goes_first_on_the_next_call(
    meta: httpx.AsyncClient,
    registry: Registry,
    routes: MemoryRoutes,
    threads: Threads,
    store: MemoryStore,
    llm: FakeLLM,
) -> None:
    llm.script.extend((Scripted(chunks=("Sí.",)), Scripted(chunks=("Te la reservo.",))))
    await routes.put(the_operators_row(AGENT))
    await delivered(meta, a_body(a_text(HOLA)))
    assert threads.of(THE_CLINICS_NUMBER, ANA) is None
    assert [kept["text"] for kept in await written(store, WAITING)] == [HOLA]

    await registry.register(AN_APP, A_RECORD.org, PRODUCTION, AGENT)
    await delivered(meta, a_body(a_text("dale", id="wamid.two")))
    thread = await quiet(threads)
    turns = [
        e.data["text"] for e in await store.since(thread.session.call) if e.type == "turn.user"
    ]
    assert turns == [HOLA, "dale"], "what waited goes first, in order"
    assert await written(store, TAKEN) == [{"message_id": "wamid.one", "call": thread.session.call}]
    assert threads.waiting.waiting == ()


async def kept_on_the_log(logs: Logs, message_id: str, received_at: float) -> None:
    """One message waiting, as the webhook of an earlier process wrote it."""
    await logs.writing_agent(AGENT).append(
        WAITING,
        {
            "channel": "whatsapp",
            "env": PRODUCTION,
            "number": THE_CLINICS_NUMBER,
            "phone_number_id": "pnid",
            "from": ANA,
            "name": "Ana",
            "message_id": message_id,
            "text": HOLA,
            "received_at": received_at,
        },
    )


async def test_the_room_answers_once_somebody_holds_the_agent_and_lets_the_expired_go(
    logs: Logs, store: MemoryStore
) -> None:
    await kept_on_the_log(logs, "stale", time.time() - WINDOW_SECONDS - 1)
    await kept_on_the_log(logs, "fresh", time.time())
    room = WaitingRoom(WINDOW_SECONDS)
    await room.loaded(store)
    held = {"yes": False}
    answered: list[str] = []

    async def answer(waiting: Waiting) -> str:
        answered.append(waiting.inbound.message_id)
        return "call_1"

    await room.answered(logs, lambda _env, _agent: held["yes"], answer)
    assert answered == [] and [w.inbound.message_id for w in room.waiting] == ["fresh"]
    held["yes"] = True
    await room.answered(logs, lambda _env, _agent: held["yes"], answer)
    assert answered == ["fresh"] and room.waiting == ()
    assert await written(store, TAKEN) == [
        {"message_id": "stale", "call": None},
        {"message_id": "fresh", "call": "call_1"},
    ]


async def test_what_was_waiting_when_a_gateway_starts_is_read_back_off_the_log(
    logs: Logs, store: MemoryStore
) -> None:
    before = WaitingRoom(WINDOW_SECONDS)
    row = the_operators_row(AGENT)
    for message_id in ("one", "two"):
        inbound = Inbound(THE_CLINICS_NUMBER, "pnid", ANA, None, message_id, "text", HOLA)
        await before.kept(logs, row, inbound)
    await before.taken(logs, before.waiting[0], "call_1")

    after = WaitingRoom(WINDOW_SECONDS)
    await after.loaded(store)
    assert [w.inbound.message_id for w in after.waiting] == ["two"]
    assert after.waiting[0].inbound.text == HOLA and after.waiting[0].env == row.env
