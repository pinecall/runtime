"""The inbox says something to a contact on WhatsApp: on the open conversation, as the agent."""

from __future__ import annotations

import httpx
import pytest

from pinecall.api.whatsapp.threads import Threads
from pinecall.live.registry import Registry
from pinecall.log.store import MemoryStore
from pinecall.routes.records_memory import MemoryRoutes
from pinecall_testkit.fake_llm import FakeLLM, Scripted
from tests.api.conftest import AGENT
from tests.api.fake_graph import FakeGraph
from tests.api.whatsapp.conftest import (
    ANA,
    THE_PHONE_NUMBER_ID,
    a_body,
    a_text,
    delivered,
    quiet,
    the_clinic_answers_at_the_number,
)

pytestmark = pytest.mark.unit

FROM_THE_INBOX = "Le escribo desde la clínica: ya tiene la cita confirmada."


async def test_a_message_reaches_the_contact_and_lands_on_the_conversations_log(
    meta: httpx.AsyncClient,
    registry: Registry,
    routes: MemoryRoutes,
    threads: Threads,
    store: MemoryStore,
    llm: FakeLLM,
    graph: FakeGraph,
) -> None:
    llm.script.append(Scripted(chunks=("Hola, dígame.",)))
    await the_clinic_answers_at_the_number(registry, routes)
    await delivered(meta, a_body(a_text("Hola")))
    call = (await quiet(threads)).session.call
    contact = (await store.facts_of([call]))[call].contact
    inbox = (await meta.get(f"/v1/agents/{AGENT}/threads")).json()
    assert [(line["contact"], line["unread"]) for line in inbox["threads"]] == [(contact, 1)]

    said = await meta.post(
        f"/v1/agents/{AGENT}/threads/{contact}/messages", json={"text": FROM_THE_INBOX}
    )
    assert (said.status_code, said.json()) == (202, {"contact": contact, "call": call})
    assert graph.sent[-1] == (THE_PHONE_NUMBER_ID, ANA, FROM_THE_INBOX)
    thread = (await meta.get(f"/v1/agents/{AGENT}/threads/{contact}")).json()
    assert [(one["kind"], one["text"]) for one in thread["messages"]] == [
        ("in", "Hola"),
        ("out", "Hola, dígame."),
        ("out", FROM_THE_INBOX),
    ]
