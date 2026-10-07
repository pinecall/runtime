"""An app socket held on one gateway, reached from another: a console's ask, the list, a stop."""

import asyncio
from collections.abc import Callable

from pinecall.domain.names import JsonObject
from pinecall.domain.scope import Scope
from pinecall.gateway._sockets import HELD_CHANNEL
from pinecall.gateway.calls.inbox import SOCKETS_CHANNEL
from tests.conftest import AGENT, Knocking, postgres, received, sent
from tests.gateway.api.conftest import a_companion, an_app


async def heard_on(second: Knocking, channel: str, until: Callable[[], bool]) -> None:
    """Wait, share by share, until the other gateway heard what the first said."""
    listening = await second.gateway.connections.signal.subscribe(channel)
    await asyncio.sleep(0)
    try:
        async with asyncio.timeout(2):
            while not until():
                await anext(listening)
                await asyncio.sleep(0)
    finally:
        listening.close()


@postgres
async def test_a_consoles_ask_on_one_gateway_is_answered_by_the_app_on_the_other(
    knocking: Knocking, knocking_two: Knocking
) -> None:
    socket = await a_companion(knocking)
    own = Scope(knocking.org.id, "sandbox")
    await heard_on(
        knocking_two, HELD_CHANNEL, lambda: knocking_two.gateway.sockets.of(own, AGENT) is not None
    )
    async with knocking_two.http(knocking.app["sandbox"]) as console:
        asking = asyncio.create_task(
            console.post(f"/v1/agents/{AGENT}/dev/chat/chat.roster", json={})
        )
        request = await received(socket)
        answer: JsonObject = {"id": request.data["id"], "result": {"chats": []}}
        await sent(socket, "dev.answer", answer)
        answered = await asyncio.wait_for(asking, 5)
    assert request.type == "dev.request"
    assert answered.json() == {"chats": []}
    await socket.close()


@postgres
async def test_an_app_held_on_one_gateway_is_listed_and_stopped_through_the_other(
    knocking: Knocking, knocking_two: Knocking
) -> None:
    socket = await an_app(knocking)
    live = knocking_two.gateway.live
    await heard_on(
        knocking_two, SOCKETS_CHANNEL, lambda: bool(live.processes_of(knocking.org.id, "sandbox"))
    )
    async with knocking_two.http(knocking.app["sandbox"]) as console:
        listed = (await console.get("/v1/apps")).json()["apps"]
        (row,) = listed
        stopped = await console.post(f"/v1/apps/{row['app']}/stop")
    assert AGENT in row["agents"] or row["agents"] == []
    assert stopped.status_code == 200
    stop = await received(socket)
    assert (stop.type, stop.data["code"]) == ("error", "stopped")
    await socket.close()
