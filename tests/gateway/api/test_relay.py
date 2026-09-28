"""Tests for the console's asks of a running app."""

import asyncio

from pinecall.domain.names import JsonObject
from tests.conftest import (
    AGENT,
    Knocking,
    postgres,
    received,
    sent,
)
from tests.gateway.api.conftest import an_app


@postgres
async def test_a_dev_verb_travels_down_the_socket_and_the_answer_comes_back(
    knocking: Knocking,
) -> None:
    socket = await an_app(knocking)
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
    socket = await an_app(knocking)
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
async def test_nobody_holding_is_404_and_a_console_alone_is_409(knocking: Knocking) -> None:
    async with knocking.http(knocking.app["sandbox"]) as console:
        nobody = await console.post(f"/v1/agents/{AGENT}/dev/chat/chat.roster", json={})
        found = await an_app(knocking, console=True)
        only_a_console = await console.post(f"/v1/agents/{AGENT}/dev/chat/chat.roster", json={})
    assert (nobody.status_code, only_a_console.status_code) == (404, 409)
    await found.close()
