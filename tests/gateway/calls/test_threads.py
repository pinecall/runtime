"""Tests for WhatsApp's threads: one per contact, in order, closed when quiet, kept."""

import asyncio
import hashlib
import hmac
import json
import time
from collections.abc import Callable

import httpx
import pytest
from websockets.asyncio.client import ClientConnection

from pinecall.channels import routes, whatsapp
from pinecall.channels.routes import RouteWrite
from pinecall.domain.call import Route
from pinecall.domain.names import Env, JsonObject
from pinecall.domain.scope import Scope
from pinecall.providers import catalog
from pinecall.tenancy import admission, vault
from tests.conftest import AGENT, Knocking, configured, postgres, received_until, sent
from tests.fakes.meta import Graph

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
    await vault.put_box_credentials(
        knocking.gateway.connections.pool, knocking.gateway.connections.vault, "whatsapp", meta
    )
    route = Route(org=knocking.org.id, agent=AGENT, channel="whatsapp", number=OUR_NUMBER, env=env)
    await routes.put(knocking.gateway.connections.pool, route, RouteWrite("hooked"))


async def an_app(knocking: Knocking, env: Env = "sandbox") -> ClientConnection:
    """An app holding the agent in the world, declared."""
    socket = await knocking.socket("/v1/apps", knocking.app[env])
    await sent(socket, "agent.register", {"routes": []})
    await received_until(socket, "agent.registered")
    await sent(socket, "agent.configure", {"config": {}})
    await received_until(socket, "agent.configured")
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


async def until(data: Callable[[], bool], within_s: float = 5.0) -> None:
    """Wait for something the conversation does on its own time."""
    deadline = time.monotonic() + within_s
    while not data():
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
    """The threads the box has open."""
    return [thread.session.call.context.call for thread in knocking.gateway.threads.open.values()]


@postgres
async def test_a_signed_message_opens_a_whatsapp_call_and_the_answer_reaches_meta(
    knocking: Knocking, graph: Graph
) -> None:
    await catalog.configure(
        knocking.gateway.connections.pool, configured([["¡Hola Ana! ¿Qué día?"]])
    )
    await a_whatsapp_line(knocking)
    app = await an_app(knocking)
    assert await written(knocking, "quiero un turno") == 200
    await until(lambda: len(graph.sent) == 1)
    assert graph.sent[0]["to"] == ANA
    assert graph.sent[0]["text"] == {"body": "¡Hola Ana! ¿Qué día?", "preview_url": False}
    (call,) = calls_open(knocking)
    kinds = [item.type for item in await knocking.gateway.logs.store.whole(call)]
    assert kinds[0] == "call.started"
    assert "turn.user" in kinds
    assert "turn.agent" in kinds
    await closed(app)


@postgres
async def test_the_second_message_of_a_contact_stays_on_the_same_call_and_another_gets_their_own(
    knocking: Knocking, graph: Graph
) -> None:
    await catalog.configure(
        knocking.gateway.connections.pool, configured([["uno"], ["dos"], ["tres"]])
    )
    await a_whatsapp_line(knocking)
    app = await an_app(knocking)
    await written(knocking, "hola", message="wamid.1")
    await until(lambda: len(graph.sent) == 1)
    await written(knocking, "sigo", message="wamid.2")
    await until(lambda: len(graph.sent) == 2)
    assert len(calls_open(knocking)) == 1
    await written(knocking, "soy otra", sender="59899000002", message="wamid.3")
    await until(lambda: len(graph.sent) == 3)
    assert len(calls_open(knocking)) == 2
    await closed(app)


@postgres
async def test_a_message_meta_refuses_leaves_an_error_entry_in_the_calls_own_log(
    knocking: Knocking, graph: Graph
) -> None:
    graph.refusal = (400, "Message failed to send because more than 24 hours have passed")
    await catalog.configure(knocking.gateway.connections.pool, configured([["hola"]]))
    await a_whatsapp_line(knocking)
    app = await an_app(knocking)
    await written(knocking, "hola")
    await until(lambda: bool(calls_open(knocking)))
    (call,) = calls_open(knocking)

    async def refused() -> bool:
        entries = await knocking.gateway.logs.store.whole(call)
        return any(
            entry.type == "error" and entry.data["code"] == "whatsapp_not_sent" for entry in entries
        )

    deadline = time.monotonic() + 5
    while not await refused():
        assert time.monotonic() < deadline
        await asyncio.sleep(0.05)
    await closed(app)


@postgres
async def test_silence_seals_the_call_and_the_next_message_opens_a_new_one(
    knocking: Knocking, graph: Graph
) -> None:
    await catalog.configure(knocking.gateway.connections.pool, configured([["uno"], ["dos"]]))
    await a_whatsapp_line(knocking)
    app = await an_app(knocking)
    knocking.gateway.threads.idle_s = 0.3
    await written(knocking, "hola", message="wamid.1")
    await until(lambda: len(graph.sent) == 1)
    (first,) = calls_open(knocking)
    await until(lambda: not calls_open(knocking))
    assert await knocking.gateway.logs.store.sealed(first)
    ended = [
        item for item in await knocking.gateway.logs.store.whole(first) if item.type == "call.ended"
    ]
    assert ended[0].data["reason"] == "timeout"
    await written(knocking, "volví", message="wamid.2")
    await until(lambda: len(graph.sent) == 2)
    assert calls_open(knocking) != [first]
    await closed(app)


@postgres
async def test_a_message_nobody_can_answer_is_kept_and_answered_once_somebody_holds_the_agent(
    knocking: Knocking, graph: Graph
) -> None:
    await catalog.configure(knocking.gateway.connections.pool, configured([["ahora sí"]]))
    await a_whatsapp_line(knocking)
    assert await written(knocking, "¿hay alguien?") == 200
    assert graph.sent == []
    (kept,) = knocking.gateway.threads.waiting
    assert kept.inbound.text == "¿hay alguien?"
    app = await an_app(knocking)
    await until(lambda: len(graph.sent) == 1)
    assert knocking.gateway.threads.waiting == []
    taken = [
        item
        for item in await knocking.gateway.logs.store.whole(f"@{AGENT}")
        if item.type == "message.taken"
    ]
    assert taken[0].data["call"] == calls_open(knocking)[0]
    await closed(app)


@postgres
async def test_what_was_waiting_when_a_gateway_starts_is_read_back_off_the_log(
    knocking: Knocking,
) -> None:
    await a_whatsapp_line(knocking)
    await written(knocking, "¿hay alguien?")
    knocking.gateway.threads.waiting = []
    await knocking.gateway.threads.loaded()
    assert [waiter.inbound.text for waiter in knocking.gateway.threads.waiting] == ["¿hay alguien?"]


@postgres
async def test_the_world_a_row_names_is_the_world_the_conversation_runs_in(
    knocking: Knocking, graph: Graph
) -> None:
    await catalog.configure(knocking.gateway.connections.pool, configured([["producción"]]))
    await a_whatsapp_line(knocking, "production")
    sandbox = await an_app(knocking, "sandbox")
    await written(knocking, "hola")
    assert graph.sent == []
    production = await an_app(knocking, "production")
    await until(lambda: len(graph.sent) == 1)
    (thread,) = knocking.gateway.threads.open.values()
    assert thread.session.call.context.route.env == "production"
    await closed(sandbox)
    await closed(production)


@postgres
async def test_a_turn_past_the_orgs_quota_closes_the_conversation_unanswered(
    knocking: Knocking, graph: Graph
) -> None:
    await catalog.configure(knocking.gateway.connections.pool, configured([["hola"]]))
    await a_whatsapp_line(knocking)
    quotas = await admission.quotas_of(
        knocking.gateway.connections.pool, knocking.org.id, "sandbox"
    )
    await admission.set_quotas(
        knocking.gateway.connections.pool, knocking.org.id, "sandbox", quotas.__class__(messages=0)
    )
    app = await an_app(knocking)
    await written(knocking, "hola")
    await until(lambda: not calls_open(knocking) and not knocking.gateway.threads.answering_now)
    assert graph.sent == []
    await closed(app)


@postgres
async def test_a_message_meta_delivers_again_is_answered_200_and_heard_once(
    knocking: Knocking, graph: Graph
) -> None:
    await catalog.configure(knocking.gateway.connections.pool, configured([["uno"], ["dos"]]))
    await a_whatsapp_line(knocking)
    app = await an_app(knocking)
    assert await written(knocking, "hola", message="wamid.1") == 200
    await until(lambda: len(graph.sent) == 1)
    assert await written(knocking, "hola", message="wamid.1") == 200
    assert await written(knocking, "sigo", message="wamid.2") == 200
    await until(lambda: len(graph.sent) == 2)
    (call,) = calls_open(knocking)
    heard = [
        item.data["text"]
        for item in await knocking.gateway.logs.store.whole(call)
        if item.type == "turn.user"
    ]
    assert heard == ["hola", "sigo"]
    await closed(app)


@postgres
async def test_a_message_kept_for_nobody_is_kept_once_however_often_meta_delivers_it(
    knocking: Knocking,
) -> None:
    await a_whatsapp_line(knocking)
    assert await written(knocking, "¿hay alguien?") == 200
    assert await written(knocking, "¿hay alguien?") == 200
    assert len(knocking.gateway.threads.waiting) == 1


@postgres
async def test_a_message_whose_reading_failed_midway_is_read_when_meta_delivers_it_again(
    knocking: Knocking, monkeypatch: pytest.MonkeyPatch
) -> None:
    await a_whatsapp_line(knocking)
    kept = whatsapp.kept

    async def broken(*_: object) -> whatsapp.Waiting:
        raise OSError("the log's disk went away")

    monkeypatch.setattr(whatsapp, "kept", broken)
    assert await written(knocking, "¿hay alguien?") == 500
    assert knocking.gateway.threads.waiting == []
    monkeypatch.setattr(whatsapp, "kept", kept)
    assert await written(knocking, "¿hay alguien?") == 200
    assert [waiter.inbound.text for waiter in knocking.gateway.threads.waiting] == ["¿hay alguien?"]


@postgres
async def test_a_message_another_delivery_is_still_reading_is_503_so_meta_keeps_it(
    knocking: Knocking,
) -> None:
    await a_whatsapp_line(knocking)
    pool = knocking.gateway.connections.pool
    assert await whatsapp.claimed(pool, knocking.org.id, "wamid.1", time.time()) == "new"
    assert await written(knocking, "¿hay alguien?", message="wamid.1") == 503
    assert knocking.gateway.threads.waiting == []


# ── the desk on a conversation ──


async def verb(knocking: Knocking, call: str, data: JsonObject) -> int:
    """A person at the desk sending one verb to the call."""
    async with knocking.http(knocking.app["sandbox"]) as desk:
        return (await desk.post(f"/v1/calls/{call}/verbs", json=data)).status_code


@postgres
async def test_while_the_desk_holds_the_thread_the_contact_hears_only_the_human(
    knocking: Knocking, graph: Graph
) -> None:
    await catalog.configure(
        knocking.gateway.connections.pool, configured([["hola"], ["de nuevo el agente"]])
    )
    await a_whatsapp_line(knocking)
    app = await an_app(knocking)
    await written(knocking, "hola", message="wamid.1")
    await until(lambda: len(graph.sent) == 1)
    (call,) = calls_open(knocking)
    assert await verb(knocking, call, {"verb": "takeover"}) == 202
    await written(knocking, "¿hay una persona?", message="wamid.2")
    async with knocking.http(knocking.app["sandbox"]) as desk:
        text = await desk.post(
            f"/v1/agents/{AGENT}/threads/+{ANA}/messages", json={"text": "Sí, soy Laura."}
        )
    assert text.status_code == 202
    assert text.json() == {"contact": f"+{ANA}", "call": call}
    await until(lambda: len(graph.sent) == 2)
    assert graph.sent[1]["text"] == {"body": "Sí, soy Laura.", "preview_url": False}
    kinds = [item.type for item in await knocking.gateway.logs.store.whole(call)]
    assert kinds.count("turn.user") == 2
    assert await verb(knocking, call, {"verb": "release"}) == 202
    await written(knocking, "gracias", message="wamid.3")
    await until(lambda: len(graph.sent) == 3)
    assert graph.sent[2]["text"] == {"body": "de nuevo el agente", "preview_url": False}
    await closed(app)


@postgres
async def test_the_desk_can_end_a_thread_and_the_log_says_a_supervisor_did(
    knocking: Knocking, graph: Graph
) -> None:
    await catalog.configure(knocking.gateway.connections.pool, configured([["hola"]]))
    await a_whatsapp_line(knocking)
    app = await an_app(knocking)
    await written(knocking, "hola")
    await until(lambda: len(graph.sent) == 1)
    (call,) = calls_open(knocking)
    assert await verb(knocking, call, {"verb": "end"}) == 202
    await until(lambda: not calls_open(knocking))
    ended = [
        item for item in await knocking.gateway.logs.store.whole(call) if item.type == "call.ended"
    ]
    assert ended[0].data["ended_by"] == "supervisor"
    await closed(app)


@postgres
async def test_the_inbox_lists_the_contact_reads_the_thread_and_marks_it_read(
    knocking: Knocking, graph: Graph
) -> None:
    await catalog.configure(knocking.gateway.connections.pool, configured([["¿Qué día?"]]))
    await a_whatsapp_line(knocking)
    app = await an_app(knocking)
    await written(knocking, "quiero un turno")
    await until(lambda: len(graph.sent) == 1)
    (call,) = calls_open(knocking)
    await knocking.gateway.live.calls[call].log.append("state.changed", {"changes": []})
    async with knocking.http(knocking.app["sandbox"]) as inbox:
        await until(lambda: True)
        listed = (await inbox.get(f"/v1/agents/{AGENT}/threads")).json()
        thread = (await inbox.get(f"/v1/agents/{AGENT}/threads/+{ANA}")).json()
        read = await inbox.post(f"/v1/agents/{AGENT}/threads/+{ANA}/read")
        after = (await inbox.get(f"/v1/agents/{AGENT}/threads")).json()
        nobody = await inbox.get(f"/v1/agents/{AGENT}/threads/+59800000000")
    (line,) = listed["threads"]
    assert (line["contact"], line["channel_last"], line["calls"]) == (f"+{ANA}", "whatsapp", 1)
    assert [(item["kind"], item["text"]) for item in thread["messages"]] == [
        ("in", "quiero un turno"),
        ("out", "¿Qué día?"),
    ]
    assert read.status_code == 204
    assert after["threads"][0]["unread"] == 0
    assert nobody.status_code == 404
    await closed(app)


@postgres
async def test_a_message_from_the_desk_is_refused_outside_whatsapp_and_on_a_sealed_thread(
    knocking: Knocking, graph: Graph
) -> None:
    await catalog.configure(knocking.gateway.connections.pool, configured([["hola"]]))
    await a_whatsapp_line(knocking)
    app = await an_app(knocking)
    knocking.gateway.threads.idle_s = 0.2
    await written(knocking, "hola")
    await until(lambda: len(graph.sent) == 1)
    await until(lambda: not calls_open(knocking))
    async with knocking.http(knocking.app["sandbox"]) as desk:
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


# The thread is held by the gateway that opened it: Meta's next delivery lands on the other, which
# hands it on, and the contact goes on in one conversation, answered once.
@postgres
async def test_a_message_landing_on_another_gateway_goes_on_in_the_thread_its_holder_runs(
    knocking: Knocking, knocking_two: Knocking, graph: Graph
) -> None:
    await catalog.configure(knocking.gateway.connections.pool, configured([["uno"], ["dos"]]))
    await a_whatsapp_line(knocking)
    app = await an_app(knocking)
    await written(knocking, "hola", message="wamid.1")
    await until(lambda: len(graph.sent) == 1)
    owners = knocking_two.gateway.live.owners
    await until(
        lambda: (
            bool(owners.shared.theirs)
            and any(share.share.threads for share in owners.shared.theirs.values())
        )
    )
    await until(
        lambda: (
            knocking_two.gateway.sockets.of(Scope(knocking.org.id, "sandbox"), AGENT) is not None
        )
    )
    assert await written(knocking_two, "sigo", message="wamid.2") == 200
    await until(lambda: len(graph.sent) == 2)
    (call,) = calls_open(knocking)
    assert knocking_two.gateway.threads.open == {}
    kinds = [item.type for item in await knocking.gateway.logs.store.whole(call)]
    assert kinds.count("turn.user") == 2
    await closed(app)
