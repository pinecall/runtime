"""Tests for a written call over the chat socket."""

import asyncio
import json
from urllib.parse import quote

from livekit.agents.llm import ChatMessage

from pinecall.gateway._deps import POLICY_VIOLATION
from pinecall.gateway.api.chat import NOT_A_STATE
from pinecall.providers import catalog
from pinecall.tenancy import personas
from pinecall.tenancy.personas import Persona, PersonaEdit
from tests.conftest import (
    AGENT,
    Knocking,
    configured,
    postgres,
    received_until,
)
from tests.fakes.acme import AcmeLLM
from tests.gateway.api.conftest import an_app


@postgres
async def test_a_text_call_is_answered_by_the_model_and_ends_sealed(knocking: Knocking) -> None:
    await catalog.configure(
        knocking.gateway.connections.pool, configured([["hola, soy la agenda"]])
    )
    app = await an_app(knocking)
    chat = await knocking.socket(f"/v1/chat?agent={AGENT}", knocking.app["sandbox"])
    started = await received_until(chat, "call.started")
    await chat.send(json.dumps({"text": "quiero un turno"}))
    answered = await received_until(chat, "turn.agent")
    assert answered.data["text"] == "hola, soy la agenda"
    await chat.close()
    call = started.call or ""
    for _ in range(50):
        if await knocking.gateway.logs.store.sealed(call):
            break
        await asyncio.sleep(0.1)
    kinds = [entry.type for entry in await knocking.gateway.logs.store.whole(call)]
    assert kinds[-3:] == ["call.ended", "call.summary", "call.score"]
    await app.close()


# The rules of the agent's own persona ride the call; another agent's of the same name do not.
@postgres
async def test_a_persona_named_on_a_chat_is_the_agents_own_and_its_rules_ride_the_call(
    knocking: Knocking,
) -> None:
    pool = knocking.gateway.connections.pool
    await catalog.configure(pool, configured([["hola"], ["hola"]]))
    mine = Persona(name="apurado", goal="g", style="s", accepts_when="a Tuesday slot")
    theirs = Persona(name="lento", goal="g", style="s", accepts_when="any slot")
    await personas.put_persona(pool, knocking.org.id, AGENT, mine, PersonaEdit("a"))
    await personas.put_persona(pool, knocking.org.id, "tienda-sur", theirs, PersonaEdit("a"))
    app = await an_app(knocking)
    rules: list[tuple[object, object]] = []
    for named in ("apurado", "lento"):
        path = f"/v1/chat?agent={AGENT}&persona={named}"
        chat = await knocking.socket(path, knocking.app["sandbox"])
        started = await received_until(chat, "call.started")
        rules.append((started.data.get("persona"), started.data.get("accepts_when")))
        await chat.close()
    assert rules == [("apurado", "a Tuesday slot"), ("lento", None)]
    await app.close()


@postgres
async def test_the_state_a_chat_asks_for_is_the_one_its_call_starts_in(knocking: Knocking) -> None:
    app = await an_app(knocking)
    state = quote(json.dumps({"patient_name": "Ana", "step": 2}))
    chat = await knocking.socket(f"/v1/chat?agent={AGENT}&state={state}", knocking.app["sandbox"])
    started = await received_until(app, "call.started")
    assert started.data["state"] == {"patient_name": "Ana", "step": 2}
    await chat.close()
    await app.close()


@postgres
async def test_a_state_that_is_not_an_object_closes_the_chat_with_the_reason(
    knocking: Knocking,
) -> None:
    app = await an_app(knocking)
    chat = await knocking.socket(f"/v1/chat?agent={AGENT}&state=%5B1%5D", knocking.app["sandbox"])
    await chat.wait_closed()
    assert (chat.close_code, chat.close_reason) == (POLICY_VIOLATION, NOT_A_STATE)
    await app.close()


@postgres
async def test_a_text_call_to_an_agent_nobody_holds_is_closed_with_the_reason(
    knocking: Knocking,
) -> None:
    chat = await knocking.socket(f"/v1/chat?agent={AGENT}", knocking.app["sandbox"])
    await chat.wait_closed()
    assert chat.close_code == POLICY_VIOLATION
    assert "no app is holding" in (chat.close_reason or "")


@postgres
async def test_a_caller_back_on_a_forgotten_call_carries_on_and_nothing_starts_again(
    knocking: Knocking,
) -> None:
    await catalog.configure(knocking.gateway.connections.pool, configured([["hola"]]))
    app = await an_app(knocking)
    chat = await knocking.socket(f"/v1/chat?agent={AGENT}", knocking.app["sandbox"])
    started = await received_until(chat, "call.started")
    call = started.call or ""
    await chat.send(json.dumps({"text": "quiero un turno"}))
    await received_until(chat, "turn.agent")
    await chat.close(code=1012)
    await asyncio.sleep(0.2)
    store = knocking.gateway.logs.store
    before = await store.written(call)
    knocking.gateway.live.close(call)
    knocking.gateway.logs.forget(call)
    back = await knocking.socket(f"/v1/chat?agent={AGENT}&call={call}", knocking.app["sandbox"])
    attached = await received_until(app, "call.attached")
    await back.send(json.dumps({"text": "sigo"}))
    await received_until(back, "turn.agent")
    session = knocking.gateway.live.calls[call].session
    assert session is not None
    await session.call.writing.flushed(5)
    assert session.call.writing.refused == []
    assert before > 0
    assert session.call.writing.after == await store.written(call) > before
    (model,) = session.built
    assert isinstance(model, AcmeLLM)
    heard = [
        item.text_content for item in model.requests[-1].items if isinstance(item, ChatMessage)
    ]
    await back.close()
    assert attached.call == call
    assert "quiero un turno" in heard
    kept = await store.whole(call)
    kinds = [item.type for item in kept]
    assert kinds.count("call.started") == 1
    assert "call.ended" not in kinds[: kinds.index("call.attached")]
    heard_by_the_log = [item.data["text"] for item in kept if item.type == "turn.user"]
    assert heard_by_the_log == ["quiero un turno", "sigo"]
    await app.close()


@postgres
async def test_a_call_that_is_over_is_not_taken_up(knocking: Knocking) -> None:
    await catalog.configure(knocking.gateway.connections.pool, configured([["hola"]]))
    app = await an_app(knocking)
    chat = await knocking.socket(f"/v1/chat?agent={AGENT}", knocking.app["sandbox"])
    started = await received_until(chat, "call.started")
    call = started.call or ""
    await chat.close()
    for _ in range(50):
        if await knocking.gateway.logs.store.sealed(call):
            break
        await asyncio.sleep(0.1)
    back = await knocking.socket(f"/v1/chat?agent={AGENT}&call={call}", knocking.app["sandbox"])
    await back.wait_closed()
    assert back.close_code == POLICY_VIOLATION
    assert "cannot be taken up" in (back.close_reason or "")
    await app.close()


@postgres
async def test_a_supervisors_end_on_a_chat_seals_the_call_and_closes_the_socket(
    knocking: Knocking,
) -> None:
    await catalog.configure(knocking.gateway.connections.pool, configured([["hola"]]))
    app = await an_app(knocking)
    chat = await knocking.socket(f"/v1/chat?agent={AGENT}", knocking.app["sandbox"])
    started = await received_until(chat, "call.started")
    call = started.call or ""
    await chat.send(json.dumps({"text": "quiero un turno"}))
    await received_until(chat, "turn.agent")
    async with knocking.http(knocking.app["sandbox"]) as desk:
        ended = await desk.post(f"/v1/calls/{call}/verbs", json={"verb": "end"})
    assert ended.status_code == 202
    await chat.wait_closed()
    assert "supervisor_ended" in (chat.close_reason or "")
    kinds = [item.type for item in await knocking.gateway.logs.store.whole(call)]
    assert "supervisor.ended" in kinds
    assert kinds[-3:] == ["call.ended", "call.summary", "call.score"]
    assert await knocking.gateway.logs.store.sealed(call)
    await app.close()


# A caller gone while the answer is on its way: the send fails before the loop hears the close.
@postgres
async def test_a_caller_who_leaves_before_the_answer_ends_the_call(knocking: Knocking) -> None:
    await catalog.configure(knocking.gateway.connections.pool, configured([["hola"]]))
    app = await an_app(knocking)
    chat = await knocking.socket(f"/v1/chat?agent={AGENT}", knocking.app["sandbox"])
    started = await received_until(chat, "call.started")
    await chat.send(json.dumps({"text": "quiero un turno"}))
    await chat.close()
    call = started.call or ""
    for _ in range(50):
        if await knocking.gateway.logs.store.sealed(call):
            break
        await asyncio.sleep(0.1)
    ended = [e for e in await knocking.gateway.logs.store.whole(call) if e.type == "call.ended"]
    assert ended[0].data["reason"] == "caller_hung_up"
    await app.close()
