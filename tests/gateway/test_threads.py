"""Tests for WhatsApp's conversations: one per contact, in order, closed when quiet, kept."""

import asyncio
import hashlib
import hmac
import json
import time
from collections.abc import Callable

import httpx
from websockets.asyncio.client import ClientConnection

from pinecall.channels import routes
from pinecall.domain.types import Env, JsonObject, Route
from pinecall.providers import catalog
from pinecall.tenancy import orgs, vault
from tests.conftest import AGENT, Knocking, configured, postgres, said_until, sent
from tests.fakes import Graph

SIGNING = "the app's own signing word"
OUR_NUMBER = "+59829001199"
ANA = "59899000001"


async def a_whatsapp_line(knocking: Knocking, env: Env = "sandbox") -> None:
    """The box's Meta app, and the org's WhatsApp number routed to the agent in the world."""
    meta: JsonObject = {
        "app_secret": SIGNING,
        "verify_token": "a word",
        "access_token": "the box's",
    }
    await vault.put_box_credentials(knocking.box.pool, knocking.box.vault, "whatsapp", meta)
    route = Route(org=knocking.org.id, agent=AGENT, channel="whatsapp", number=OUR_NUMBER, env=env)
    await routes.put(knocking.box.pool, route, account=None)


async def an_app(knocking: Knocking, env: Env = "sandbox") -> ClientConnection:
    """An app holding the agent in the world, declared."""
    socket = await knocking.socket("/v1/apps", knocking.app[env])
    await sent(socket, "agent.register", {"routes": []})
    await said_until(socket, "agent.registered")
    await sent(socket, "agent.configure", {"config": {}})
    await said_until(socket, "agent.configured")
    return socket


async def written(
    knocking: Knocking, text: str, *, sender: str = ANA, message: str = "wamid.1"
) -> int:
    """Meta delivering one text to the org's number, signed; the door's status."""
    value = {
        "metadata": {"display_phone_number": OUR_NUMBER.lstrip("+"), "phone_number_id": "1055"},
        "contacts": [{"wa_id": sender, "profile": {"name": "Ana"}}],
        "messages": [{"from": sender, "id": message, "type": "text", "text": {"body": text}}],
    }
    body = json.dumps({"entry": [{"changes": [{"value": value}]}]}).encode()
    signature = "sha256=" + hmac.new(SIGNING.encode(), body, hashlib.sha256).hexdigest()
    async with httpx.AsyncClient(base_url=knocking.url) as meta:
        answer = await meta.post(
            "/v1/whatsapp/webhook", content=body, headers={"x-hub-signature-256": signature}
        )
    return answer.status_code


async def until(said: Callable[[], bool], within_s: float = 5.0) -> None:
    """Wait for something the conversation does on its own time."""
    deadline = time.monotonic() + within_s
    while not said():
        assert time.monotonic() < deadline, "it never happened"
        await asyncio.sleep(0.02)


# An app that never reads stops reading at its queue's end, and so never hears the close.
async def closed(app: ClientConnection) -> None:
    """Read what the app was sent, then close it."""
    while True:
        try:
            await asyncio.wait_for(app.recv(), 0.05)
        except TimeoutError:
            break
    await app.close()


def calls_open(knocking: Knocking) -> list[str]:
    """The conversations the box has open."""
    return [thread.session.call.context.call for thread in knocking.box.threads.open.values()]


@postgres
async def test_a_signed_message_opens_a_whatsapp_call_and_the_answer_reaches_meta(
    gateway: Knocking, graph: Graph
) -> None:
    await catalog.configure(gateway.box.pool, configured([["¡Hola Ana! ¿Qué día?"]]))
    await a_whatsapp_line(gateway)
    app = await an_app(gateway)
    assert await written(gateway, "quiero un turno") == 200
    await until(lambda: len(graph.sent) == 1)
    assert graph.sent[0]["to"] == ANA
    assert graph.sent[0]["text"] == {"body": "¡Hola Ana! ¿Qué día?", "preview_url": False}
    (call,) = calls_open(gateway)
    kinds = [one.type for one in await gateway.box.logs.store.whole(call)]
    assert kinds[0] == "call.started"
    assert "turn.user" in kinds
    assert "turn.agent" in kinds
    await closed(app)


@postgres
async def test_the_second_message_of_a_contact_stays_on_the_same_call_and_another_gets_their_own(
    gateway: Knocking, graph: Graph
) -> None:
    await catalog.configure(gateway.box.pool, configured([["uno"], ["dos"], ["tres"]]))
    await a_whatsapp_line(gateway)
    app = await an_app(gateway)
    await written(gateway, "hola", message="wamid.1")
    await until(lambda: len(graph.sent) == 1)
    await written(gateway, "sigo", message="wamid.2")
    await until(lambda: len(graph.sent) == 2)
    assert len(calls_open(gateway)) == 1
    await written(gateway, "soy otra", sender="59899000002", message="wamid.3")
    await until(lambda: len(graph.sent) == 3)
    assert len(calls_open(gateway)) == 2
    await closed(app)


@postgres
async def test_a_message_meta_refuses_leaves_an_error_entry_in_the_calls_own_log(
    gateway: Knocking, graph: Graph
) -> None:
    graph.refusal = (400, "Message failed to send because more than 24 hours have passed")
    await catalog.configure(gateway.box.pool, configured([["hola"]]))
    await a_whatsapp_line(gateway)
    app = await an_app(gateway)
    await written(gateway, "hola")
    await until(lambda: bool(calls_open(gateway)))
    (call,) = calls_open(gateway)

    async def refused() -> bool:
        entries = await gateway.box.logs.store.whole(call)
        return any(
            one.type == "error" and one.data["code"] == "whatsapp_not_sent" for one in entries
        )

    deadline = time.monotonic() + 5
    while not await refused():
        assert time.monotonic() < deadline
        await asyncio.sleep(0.05)
    await closed(app)


@postgres
async def test_silence_seals_the_call_and_the_next_message_opens_a_new_one(
    gateway: Knocking, graph: Graph
) -> None:
    await catalog.configure(gateway.box.pool, configured([["uno"], ["dos"]]))
    await a_whatsapp_line(gateway)
    app = await an_app(gateway)
    gateway.box.threads.idle_s = 0.3
    await written(gateway, "hola", message="wamid.1")
    await until(lambda: len(graph.sent) == 1)
    (first,) = calls_open(gateway)
    await until(lambda: not calls_open(gateway))
    assert await gateway.box.logs.store.sealed(first)
    ended = [one for one in await gateway.box.logs.store.whole(first) if one.type == "call.ended"]
    assert ended[0].data["reason"] == "timeout"
    await written(gateway, "volví", message="wamid.2")
    await until(lambda: len(graph.sent) == 2)
    assert calls_open(gateway) != [first]
    await closed(app)


@postgres
async def test_a_message_nobody_can_answer_is_kept_and_answered_once_somebody_holds_the_agent(
    gateway: Knocking, graph: Graph
) -> None:
    await catalog.configure(gateway.box.pool, configured([["ahora sí"]]))
    await a_whatsapp_line(gateway)
    assert await written(gateway, "¿hay alguien?") == 200
    assert graph.sent == []
    (kept,) = gateway.box.threads.waiting
    assert kept.inbound.text == "¿hay alguien?"
    app = await an_app(gateway)
    await until(lambda: len(graph.sent) == 1)
    assert gateway.box.threads.waiting == []
    taken = [
        one
        for one in await gateway.box.logs.store.whole(f"@{AGENT}")
        if one.type == "message.taken"
    ]
    assert taken[0].data["call"] == calls_open(gateway)[0]
    await closed(app)


@postgres
async def test_what_was_waiting_when_a_gateway_starts_is_read_back_off_the_log(
    gateway: Knocking,
) -> None:
    await a_whatsapp_line(gateway)
    await written(gateway, "¿hay alguien?")
    gateway.box.threads.waiting = []
    await gateway.box.threads.loaded()
    assert [one.inbound.text for one in gateway.box.threads.waiting] == ["¿hay alguien?"]


@postgres
async def test_the_world_a_row_names_is_the_world_the_conversation_runs_in(
    gateway: Knocking, graph: Graph
) -> None:
    await catalog.configure(gateway.box.pool, configured([["producción"]]))
    await a_whatsapp_line(gateway, "production")
    sandbox = await an_app(gateway, "sandbox")
    await written(gateway, "hola")
    assert graph.sent == []
    production = await an_app(gateway, "production")
    await until(lambda: len(graph.sent) == 1)
    (thread,) = gateway.box.threads.open.values()
    assert thread.session.call.context.route.env == "production"
    await closed(sandbox)
    await closed(production)


@postgres
async def test_a_turn_past_the_orgs_quota_closes_the_conversation_unanswered(
    gateway: Knocking, graph: Graph
) -> None:
    await catalog.configure(gateway.box.pool, configured([["hola"]]))
    await a_whatsapp_line(gateway)
    quotas = await orgs.quotas_of(gateway.box.pool, gateway.org.id, "sandbox")
    await orgs.set_quotas(gateway.box.pool, gateway.org.id, "sandbox", quotas.__class__(messages=0))
    app = await an_app(gateway)
    await written(gateway, "hola")
    await until(lambda: not calls_open(gateway) and not gateway.box.threads.answering_now)
    assert graph.sent == []
    await closed(app)


# ── the desk on a conversation ──


async def verb(knocking: Knocking, call: str, said: JsonObject) -> int:
    """A person at the desk sending one verb to the call."""
    async with knocking.http(knocking.app["sandbox"]) as desk:
        return (await desk.post(f"/v1/calls/{call}/verbs", json=said)).status_code


@postgres
async def test_while_the_desk_holds_the_thread_the_contact_hears_only_the_human(
    gateway: Knocking, graph: Graph
) -> None:
    await catalog.configure(gateway.box.pool, configured([["hola"], ["de nuevo el agente"]]))
    await a_whatsapp_line(gateway)
    app = await an_app(gateway)
    await written(gateway, "hola", message="wamid.1")
    await until(lambda: len(graph.sent) == 1)
    (call,) = calls_open(gateway)
    assert await verb(gateway, call, {"verb": "takeover"}) == 202
    await written(gateway, "¿hay una persona?", message="wamid.2")
    async with gateway.http(gateway.app["sandbox"]) as desk:
        said = await desk.post(
            f"/v1/agents/{AGENT}/threads/+{ANA}/messages", json={"text": "Sí, soy Laura."}
        )
    assert said.status_code == 202
    assert said.json() == {"contact": f"+{ANA}", "call": call}
    await until(lambda: len(graph.sent) == 2)
    assert graph.sent[1]["text"] == {"body": "Sí, soy Laura.", "preview_url": False}
    kinds = [one.type for one in await gateway.box.logs.store.whole(call)]
    assert kinds.count("turn.user") == 2
    assert await verb(gateway, call, {"verb": "release"}) == 202
    await written(gateway, "gracias", message="wamid.3")
    await until(lambda: len(graph.sent) == 3)
    assert graph.sent[2]["text"] == {"body": "de nuevo el agente", "preview_url": False}
    await closed(app)


@postgres
async def test_the_desk_can_end_a_thread_and_the_log_says_a_supervisor_did(
    gateway: Knocking, graph: Graph
) -> None:
    await catalog.configure(gateway.box.pool, configured([["hola"]]))
    await a_whatsapp_line(gateway)
    app = await an_app(gateway)
    await written(gateway, "hola")
    await until(lambda: len(graph.sent) == 1)
    (call,) = calls_open(gateway)
    assert await verb(gateway, call, {"verb": "end"}) == 202
    await until(lambda: not calls_open(gateway))
    ended = [one for one in await gateway.box.logs.store.whole(call) if one.type == "call.ended"]
    assert ended[0].data["ended_by"] == "supervisor"
    await closed(app)


@postgres
async def test_the_inbox_lists_the_contact_reads_the_thread_and_marks_it_read(
    gateway: Knocking, graph: Graph
) -> None:
    await catalog.configure(gateway.box.pool, configured([["¿Qué día?"]]))
    await a_whatsapp_line(gateway)
    app = await an_app(gateway)
    await written(gateway, "quiero un turno")
    await until(lambda: len(graph.sent) == 1)
    (call,) = calls_open(gateway)
    await gateway.box.live.calls[call].log.append("state.changed", {"changes": []})
    async with gateway.http(gateway.app["sandbox"]) as inbox:
        await until(lambda: True)
        listed = (await inbox.get(f"/v1/agents/{AGENT}/threads")).json()
        thread = (await inbox.get(f"/v1/agents/{AGENT}/threads/+{ANA}")).json()
        read = await inbox.post(f"/v1/agents/{AGENT}/threads/+{ANA}/read")
        after = (await inbox.get(f"/v1/agents/{AGENT}/threads")).json()
        nobody = await inbox.get(f"/v1/agents/{AGENT}/threads/+59800000000")
    (line,) = listed["threads"]
    assert (line["contact"], line["channel_last"], line["calls"]) == (f"+{ANA}", "whatsapp", 1)
    assert [(one["kind"], one["text"]) for one in thread["messages"]] == [
        ("in", "quiero un turno"),
        ("out", "¿Qué día?"),
    ]
    assert read.status_code == 204
    assert after["threads"][0]["unread"] == 0
    assert nobody.status_code == 404
    await closed(app)


@postgres
async def test_a_message_from_the_desk_is_refused_outside_whatsapp_and_on_a_sealed_thread(
    gateway: Knocking, graph: Graph
) -> None:
    await catalog.configure(gateway.box.pool, configured([["hola"]]))
    await a_whatsapp_line(gateway)
    app = await an_app(gateway)
    gateway.box.threads.idle_s = 0.2
    await written(gateway, "hola")
    await until(lambda: len(graph.sent) == 1)
    await until(lambda: not calls_open(gateway))
    async with gateway.http(gateway.app["sandbox"]) as desk:
        sealed = await desk.post(
            f"/v1/agents/{AGENT}/threads/+{ANA}/messages", json={"text": "¿sigue ahí?"}
        )
        nobody = await desk.post(
            f"/v1/agents/{AGENT}/threads/+59800000000/messages", json={"text": "hola"}
        )
    assert sealed.status_code == 409
    assert "went quiet" in sealed.json()["detail"]
    assert nobody.status_code == 404
    await closed(app)
