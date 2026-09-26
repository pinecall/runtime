"""POST /v1/whatsapp/webhook end to end: one thread per contact, and its turn back out to Meta."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from pinecall.api.whatsapp.threads import IDLE_SECONDS, WINDOW_SECONDS, Threads
from pinecall.live.registry import Registry
from pinecall.log.entry import Entry
from pinecall.log.store import MemoryStore
from pinecall.orgs.vault import Vault
from pinecall.routes.records_memory import MemoryRoutes
from pinecall.types import PRODUCTION
from pinecall.whatsapp.cloud_api import GraphRefused
from pinecall.whatsapp.outbound_replies import NOT_SENT
from tests.api.conftest import A_RECORD, AGENT
from tests.api.fake_graph import FakeGraph
from tests.api.whatsapp.conftest import (
    AN_APP,
    AN_INSTANT,
    ANA,
    SOMEBODY_ELSE,
    THE_BOXES_TOKEN,
    THE_CLINICS_NUMBER,
    THE_PHONE_NUMBER_ID,
    WEBHOOK,
    a_body,
    a_delivery_receipt,
    a_picture,
    a_text,
    delivered,
    quiet,
    the_clinic_answers_at_the_number,
    the_operators_row,
)
from tests.session.fake_llm import FakeLLM, Scripted

pytestmark = pytest.mark.unit

HOLA = "Hola, ¿tienen algo el martes?"
AN_ANSWER = "Sí, el martes a las 10:15."
ANOTHER_ANSWER = "Te la reservo."

# The org's own Meta token, as `orgs provider-key set clinica whatsapp` would have kept it.
THE_CLINICS_TOKEN = "the-clinics-own-whatsapp-token"

# The agent the operator moves the number to, so a declaration and a row can disagree at it.
THE_NIGHT_AGENT = "guardia-nocturna"

# ── the subscription handshake ──────────────────────────────────────────────────


async def test_metas_handshake_is_echoed_back_when_the_word_is_the_right_one(
    meta: httpx.AsyncClient,
) -> None:
    answer = await meta.get(WEBHOOK, params=_a_handshake("a-verify-token-nobody-will-ever-type"))
    assert answer.status_code == 200 and answer.text == "a-challenge"


async def test_a_handshake_with_another_word_echoes_nothing(meta: httpx.AsyncClient) -> None:
    answer = await meta.get(WEBHOOK, params=_a_handshake("whatever-somebody-guessed"))
    assert answer.status_code == 403 and "a-challenge" not in answer.text


# ── one message, one thread, one call ───────────────────────────────────────────


async def test_an_unsigned_body_is_403_and_opens_nothing(
    meta: httpx.AsyncClient, threads: Threads, store: MemoryStore
) -> None:
    answer = await meta.post(WEBHOOK, json=a_body(a_text(HOLA)))
    assert answer.status_code == 403
    assert threads.of(THE_CLINICS_NUMBER, ANA) is None
    assert await store.list_calls(AGENT) == []


async def test_a_signed_message_opens_a_whatsapp_call_and_the_answer_reaches_meta(
    meta: httpx.AsyncClient,
    registry: Registry,
    routes: MemoryRoutes,
    threads: Threads,
    store: MemoryStore,
    llm: FakeLLM,
    graph: FakeGraph,
) -> None:
    llm.script.append(Scripted(chunks=(AN_ANSWER,)))
    await the_clinic_answers_at_the_number(registry, routes)

    assert await delivered(meta, a_body(a_text(HOLA))) == {"received": 1}
    written = await _the_log_of(threads, store)

    started = _first(written, "call.started")
    assert started.data["channel"] == "whatsapp"
    assert started.data["from"] == f"+{ANA}"
    assert _first(written, "turn.user").data["text"] == HOLA
    assert _first(written, "turn.agent").data["text"] == AN_ANSWER
    assert graph.sent == [(THE_PHONE_NUMBER_ID, ANA, AN_ANSWER)]


async def test_the_second_message_of_a_contact_stays_on_the_same_call(
    meta: httpx.AsyncClient,
    registry: Registry,
    routes: MemoryRoutes,
    threads: Threads,
    store: MemoryStore,
    llm: FakeLLM,
) -> None:
    llm.script.extend((Scripted(chunks=(AN_ANSWER,)), Scripted(chunks=(ANOTHER_ANSWER,))))
    await the_clinic_answers_at_the_number(registry, routes)

    await delivered(meta, a_body(a_text(HOLA)))
    await delivered(meta, a_body(a_text("dale", id="wamid.two")))
    written = await _the_log_of(threads, store)

    assert await store.list_calls(AGENT) == [_first(written, "call.started").call]
    assert [entry.data["text"] for entry in written if entry.type == "turn.user"] == [HOLA, "dale"]


async def test_another_person_at_the_same_number_gets_a_call_of_their_own(
    meta: httpx.AsyncClient,
    registry: Registry,
    routes: MemoryRoutes,
    threads: Threads,
    store: MemoryStore,
    llm: FakeLLM,
) -> None:
    llm.script.extend((Scripted(chunks=(AN_ANSWER,)), Scripted(chunks=(ANOTHER_ANSWER,))))
    await the_clinic_answers_at_the_number(registry, routes)

    await delivered(meta, a_body(a_text(HOLA)))
    await delivered(meta, a_body(a_text("buenas", wa_id=SOMEBODY_ELSE, id="wamid.two")))
    hers = await _the_log_of(threads, store)
    theirs = await _the_log_of(threads, store, wa_id=SOMEBODY_ELSE)

    assert _first(hers, "call.started").call != _first(theirs, "call.started").call
    assert _first(theirs, "call.started").data["from"] == f"+{SOMEBODY_ELSE}"


async def test_a_delivery_receipt_is_200_and_opens_nothing(
    meta: httpx.AsyncClient, registry: Registry, routes: MemoryRoutes, threads: Threads
) -> None:
    await the_clinic_answers_at_the_number(registry, routes)
    assert await delivered(meta, a_delivery_receipt()) == {"received": 0}
    assert threads.of(THE_CLINICS_NUMBER, ANA) is None


async def test_an_image_is_acknowledged_and_never_opens_a_call(
    meta: httpx.AsyncClient, registry: Registry, routes: MemoryRoutes, threads: Threads
) -> None:
    """Only text is read here: a picture or a voice note is answered 200 and left alone."""
    await the_clinic_answers_at_the_number(registry, routes)
    assert await delivered(meta, a_body(a_picture())) == {"received": 1}
    assert threads.of(THE_CLINICS_NUMBER, ANA) is None


async def test_a_number_nobody_routed_is_200_and_opens_nothing(
    meta: httpx.AsyncClient, registry: Registry, threads: Threads, store: MemoryStore
) -> None:
    await registry.register(AN_APP, A_RECORD.org, PRODUCTION, AGENT)
    assert await delivered(meta, a_body(a_text(HOLA))) == {"received": 1}
    assert threads.of(THE_CLINICS_NUMBER, ANA) is None
    assert await store.list_calls(AGENT) == []


# ── who answers, and on whose token ─────────────────────────────────────────────


async def test_the_row_says_who_answers_and_moving_it_moves_the_next_message(
    meta: httpx.AsyncClient,
    registry: Registry,
    routes: MemoryRoutes,
    threads: Threads,
    store: MemoryStore,
    llm: FakeLLM,
) -> None:
    """`routes add` moves a number with no deploy, and there is nothing else that could hold it."""
    llm.script.append(Scripted(chunks=(AN_ANSWER,)))
    await the_clinic_answers_at_the_number(registry, routes)
    await registry.register(AN_APP, A_RECORD.org, PRODUCTION, THE_NIGHT_AGENT)
    await routes.put(the_operators_row(THE_NIGHT_AGENT))

    await delivered(meta, a_body(a_text(HOLA)))
    written = await _the_log_of(threads, store)

    assert _first(written, "call.started").agent == THE_NIGHT_AGENT


async def test_an_org_that_brought_its_own_meta_token_answers_on_that_one(
    meta: httpx.AsyncClient,
    registry: Registry,
    routes: MemoryRoutes,
    threads: Threads,
    vault: Vault | None,
    llm: FakeLLM,
    graph: FakeGraph,
) -> None:
    llm.script.append(Scripted(chunks=(AN_ANSWER,)))
    assert vault is not None
    await vault.put(A_RECORD.org, "whatsapp", THE_CLINICS_TOKEN)
    await the_clinic_answers_at_the_number(registry, routes)

    await delivered(meta, a_body(a_text(HOLA)))
    await quiet(threads)

    assert graph.tokens == [THE_CLINICS_TOKEN]


async def test_an_org_that_brought_none_answers_on_the_boxs_own_token(
    meta: httpx.AsyncClient,
    registry: Registry,
    routes: MemoryRoutes,
    threads: Threads,
    llm: FakeLLM,
    graph: FakeGraph,
) -> None:
    llm.script.append(Scripted(chunks=(AN_ANSWER,)))
    await the_clinic_answers_at_the_number(registry, routes)

    await delivered(meta, a_body(a_text(HOLA)))
    await quiet(threads)

    assert graph.tokens == [THE_BOXES_TOKEN]


async def test_a_box_with_no_token_anywhere_opens_no_session_at_all(
    a_box_with_no_token: None,  # noqa: ARG001 — it is the whole subject of the test
    meta: httpx.AsyncClient,
    registry: Registry,
    routes: MemoryRoutes,
    threads: Threads,
    store: MemoryStore,
) -> None:
    """Refused at the door: a call whose answer could never leave is never paid for."""
    await the_clinic_answers_at_the_number(registry, routes)
    assert await delivered(meta, a_body(a_text(HOLA))) == {"received": 1}
    assert threads.of(THE_CLINICS_NUMBER, ANA) is None
    assert await store.list_calls(AGENT) == []


async def test_a_message_meta_refuses_leaves_an_error_entry_in_the_calls_own_log(
    meta: httpx.AsyncClient,
    registry: Registry,
    routes: MemoryRoutes,
    threads: Threads,
    store: MemoryStore,
    llm: FakeLLM,
    graph: FakeGraph,
) -> None:
    llm.script.append(Scripted(chunks=(AN_ANSWER,)))
    graph.refusing = GraphRefused(400, "Recipient phone number not in allowed list")
    await the_clinic_answers_at_the_number(registry, routes)

    await delivered(meta, a_body(a_text(HOLA)))
    written = await _the_log_of(threads, store)

    refusal = _first(written, "error")
    assert refusal.data["code"] == NOT_SENT
    assert "not in allowed list" in str(refusal.data["message"])


# ── the idle close ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("idle_seconds", [AN_INSTANT])
async def test_silence_seals_the_call_and_the_next_message_opens_a_new_one(
    meta: httpx.AsyncClient,
    registry: Registry,
    routes: MemoryRoutes,
    threads: Threads,
    store: MemoryStore,
    llm: FakeLLM,
) -> None:
    llm.script.extend((Scripted(chunks=(AN_ANSWER,)), Scripted(chunks=(ANOTHER_ANSWER,))))
    await the_clinic_answers_at_the_number(registry, routes)

    await delivered(meta, a_body(a_text(HOLA)))
    first = await _the_log_of(threads, store)
    await _the_thread_goes_quiet(threads)
    await delivered(meta, a_body(a_text("¿hola?", id="wamid.two")))
    second = await _the_log_of(threads, store)

    the_first_call = _first(first, "call.started").call or ""
    sealed = await store.since(the_first_call, after=0)
    ended = _first(sealed, "call.ended")
    assert (ended.data["reason"], ended.data["ended_by"]) == ("timeout", "platform")
    assert _first(first, "call.started").call != _first(second, "call.started").call


def test_the_idle_close_is_what_honours_metas_twenty_four_hour_window() -> None:
    """No second timer: a thread that idles out at two hours can never reach twenty-four."""
    assert IDLE_SECONDS < WINDOW_SECONDS
    assert (IDLE_SECONDS, WINDOW_SECONDS) == (2 * 60 * 60, 24 * 60 * 60)


# ── the world one message needs ─────────────────────────────────────────────────


def _a_handshake(token: str) -> dict[str, str]:
    """The query Meta sends once, when a person subscribes this URL in the Meta app."""
    return {"hub.mode": "subscribe", "hub.verify_token": token, "hub.challenge": "a-challenge"}


async def _the_log_of(threads: Threads, store: MemoryStore, wa_id: str = ANA) -> list[Entry]:
    """Everything written on this contact's call, once the model has finished answering."""
    thread = await quiet(threads, wa_id)
    return await store.since(thread.session.call, after=0)


async def _the_thread_goes_quiet(threads: Threads, wa_id: str = ANA) -> None:
    """Let the idle clock run out, which in this suite is a twentieth of a second."""
    while threads.of(THE_CLINICS_NUMBER, wa_id) is not None:  # noqa: ASYNC110 — the idle clock
        await asyncio.sleep(0.01)


def _first(written: list[Entry], type: str) -> Entry:
    """The first entry of this type, or a failure naming what the log did hold instead."""
    found = next((entry for entry in written if entry.type == type), None)
    assert found is not None, f"no {type} in {[entry.type for entry in written]}"
    return found
