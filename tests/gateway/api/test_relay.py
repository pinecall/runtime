"""Tests for the console's asks of a running app."""

import asyncio

from websockets.asyncio.client import ClientConnection

from pinecall.domain.names import JsonObject
from pinecall.gateway._sockets import NO_DEV_ANSWERER
from tests.conftest import (
    AGENT,
    Knocking,
    postgres,
    received,
    sent,
)
from tests.gateway.api.conftest import a_companion, an_app


@postgres
async def test_a_dev_verb_travels_down_the_socket_and_the_answer_comes_back(
    knocking: Knocking,
) -> None:
    socket = await a_companion(knocking)
    async with knocking.http(knocking.app["sandbox"]) as console:
        params = asyncio.create_task(
            console.post(f"/v1/agents/{AGENT}/dev/chat/chat.roster", json={})
        )
        request = await received(socket)
        answer: JsonObject = {"id": request.data["id"], "result": {"chats": []}}
        await sent(socket, "dev.answer", answer)
        answered = await asyncio.wait_for(params, 5)
    assert request.type == "dev.request"
    assert answered.json() == {"chats": []}
    await socket.close()


@postgres
async def test_the_apps_refusal_is_the_consoles_status_and_sentence(knocking: Knocking) -> None:
    socket = await a_companion(knocking)
    async with knocking.http(knocking.app["sandbox"]) as console:
        params = asyncio.create_task(
            console.post(f"/v1/agents/{AGENT}/dev/knowledge/knowledge.roster", json={})
        )
        request = await received(socket)
        refusal: JsonObject = {"status": 422, "detail": "no base named x"}
        await sent(socket, "dev.answer", {"id": request.data["id"], "refused": refusal})
        answered = await asyncio.wait_for(params, 5)
    assert (answered.status_code, answered.json()["detail"]) == (422, "no base named x")
    await socket.close()


@postgres
async def test_a_verb_not_of_the_family_is_refused_before_the_app_is_asked(
    knocking: Knocking,
) -> None:
    async with knocking.http(knocking.app["sandbox"]) as console:
        refused = await console.post(f"/v1/agents/{AGENT}/dev/chat/memory.eval", json={})
    assert refused.status_code == 404


@postgres
async def test_nobody_holding_is_404_and_servers_alone_are_409_naming_pinecall_start(
    knocking: Knocking,
) -> None:
    async with knocking.http(knocking.app["sandbox"]) as console:
        nobody = await console.post(f"/v1/agents/{AGENT}/dev/chat/chat.roster", json={})
        found = await an_app(knocking)
        servers_alone = await console.post(f"/v1/agents/{AGENT}/dev/chat/chat.roster", json={})
    assert (nobody.status_code, servers_alone.status_code) == (404, 409)
    assert servers_alone.json()["detail"] == NO_DEV_ANSWERER.format(slug=AGENT)
    await found.close()


@postgres
async def test_the_panel_is_drawn_by_the_server_and_a_golden_roster_by_the_companion(
    knocking: Knocking,
) -> None:
    companion = await a_companion(knocking)
    server = await an_app(knocking)
    async with knocking.http(knocking.app["sandbox"]) as console:
        roster = asyncio.create_task(
            console.post(f"/v1/agents/{AGENT}/dev/evals/goldens.roster", json={})
        )
        asked_for_goldens = await answered(companion, {"goldens": []})
        await asyncio.wait_for(roster, 5)
        panel = asyncio.create_task(
            console.post(f"/v1/agents/{AGENT}/dev/view/view.render", json={})
        )
        asked_for_the_panel = await answered(server, {"view": None})
        await asyncio.wait_for(panel, 5)
    assert (asked_for_goldens, asked_for_the_panel) == ("goldens.roster", "view.render")
    await companion.close()
    await server.close()


async def answered(socket: ClientConnection, result: JsonObject) -> str:
    """The verb of the dev.request this socket is sent, answered with the result."""
    request = await received(socket)
    await sent(socket, "dev.answer", {"id": request.data["id"], "result": result})
    return str(request.data["verb"])
