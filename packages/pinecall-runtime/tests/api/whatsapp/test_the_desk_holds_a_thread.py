"""A human at the desk answering on WhatsApp: the six verbs, on a thread Meta is delivering to."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from pinecall.api.agents.registry import Registry
from pinecall.api.whatsapp.threads import Threads
from pinecall.log.entry import Entry
from pinecall.log.store import MemoryStore
from pinecall.routes.records_memory import MemoryRoutes
from tests.api.conftest import A_KEY
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
from tests.session.fake_llm import FakeLLM, Scripted

pytestmark = pytest.mark.unit

HOLA = "Hola, ¿tienen algo el martes?"
AN_ANSWER = "Sí, el martes a las 10:15."
THE_DESK_SAYS = "Soy Marta, del mostrador: te lo confirmo yo."


async def test_while_the_desk_holds_the_thread_the_contact_hears_only_the_human(
    meta: httpx.AsyncClient,
    registry: Registry,
    routes: MemoryRoutes,
    threads: Threads,
    store: MemoryStore,
    llm: FakeLLM,
    graph: FakeGraph,
) -> None:
    """The whole point of the verb on this channel: a person answers, from the same number."""
    llm.script.append(Scripted(chunks=(AN_ANSWER,)))
    call = await _a_thread(meta, registry, routes, threads)

    assert (await _verb(meta, call, {"verb": "takeover"})).status_code == 202
    asked = llm.requests
    await delivered(meta, a_body(a_text("¿me lo confirmás?", id="wamid.two")))
    await quiet(threads)

    # The model was never asked, and nothing left for the contact on its own.
    assert llm.requests == asked
    assert graph.sent == [(THE_PHONE_NUMBER_ID, ANA, AN_ANSWER)]

    assert (await _verb(meta, call, {"verb": "say", "text": THE_DESK_SAYS})).status_code == 202
    assert graph.sent[-1] == (THE_PHONE_NUMBER_ID, ANA, THE_DESK_SAYS)

    written = await store.since(call, after=0)
    said = _typed(written, "supervisor.said", "turn.agent")
    assert [entry.type for entry in said[-2:]] == ["supervisor.said", "turn.agent"]


async def test_the_caller_kept_writing_while_it_was_held_and_the_log_kept_all_of_it(
    meta: httpx.AsyncClient,
    registry: Registry,
    routes: MemoryRoutes,
    threads: Threads,
    store: MemoryStore,
    llm: FakeLLM,
) -> None:
    llm.script.append(Scripted(chunks=(AN_ANSWER,)))
    call = await _a_thread(meta, registry, routes, threads)

    await _verb(meta, call, {"verb": "takeover"})
    await delivered(meta, a_body(a_text("¿me lo confirmás?", id="wamid.two")))
    await quiet(threads)

    written = await store.since(call, after=0)
    assert [entry.data["text"] for entry in written if entry.type == "turn.user"] == [
        HOLA,
        "¿me lo confirmás?",
    ]


async def test_a_release_gives_the_thread_back_and_the_agent_answers_the_next_message(
    meta: httpx.AsyncClient,
    registry: Registry,
    routes: MemoryRoutes,
    threads: Threads,
    store: MemoryStore,
    llm: FakeLLM,
    graph: FakeGraph,
) -> None:
    llm.script.extend(
        (
            Scripted(chunks=(AN_ANSWER,)),
            Scripted(chunks=("Sigo yo.",)),
            Scripted(chunks=("El martes queda reservado.",)),
        )
    )
    call = await _a_thread(meta, registry, routes, threads)

    await _verb(meta, call, {"verb": "takeover"})
    assert (await _verb(meta, call, {"verb": "release"})).status_code == 202
    await delivered(meta, a_body(a_text("dale", id="wamid.two")))
    await quiet(threads)

    written = await store.since(call, after=0)
    assert _typed(written, "supervisor.released")
    assert graph.sent[-1] == (THE_PHONE_NUMBER_ID, ANA, "El martes queda reservado.")


async def test_the_desk_can_end_a_thread_and_the_log_says_a_supervisor_did(
    meta: httpx.AsyncClient,
    registry: Registry,
    routes: MemoryRoutes,
    threads: Threads,
    store: MemoryStore,
    llm: FakeLLM,
) -> None:
    llm.script.append(Scripted(chunks=(AN_ANSWER,)))
    call = await _a_thread(meta, registry, routes, threads)

    assert (await _verb(meta, call, {"verb": "end", "reason": "resuelto"})).status_code == 202

    written = await store.since(call, after=0)
    ended = _typed(written, "call.ended")[0]
    assert (ended.data["reason"], ended.data["ended_by"]) == ("supervisor_ended", "supervisor")


# ── the world one verb needs ────────────────────────────────────────────────────


async def _a_thread(
    meta: httpx.AsyncClient, registry: Registry, routes: MemoryRoutes, threads: Threads
) -> str:
    """The clinic on WhatsApp, and one conversation of it past the contact's first message."""
    await the_clinic_answers_at_the_number(registry, routes)
    await delivered(meta, a_body(a_text(HOLA)))
    return (await quiet(threads)).session.call


async def _verb(meta: httpx.AsyncClient, call: str, said: dict[str, Any]) -> httpx.Response:
    """One supervise verb at the HTTP door, with the org's own key, as a desk sends it."""
    return await meta.post(
        f"/v1/calls/{call}/verbs", json=said, headers={"Authorization": f"Bearer {A_KEY}"}
    )


def _typed(written: list[Entry], *types: str) -> list[Entry]:
    """Every entry of these types, in the order the log wrote them."""
    return [entry for entry in written if entry.type in types]
