"""Commands for a call: told on its channel, taken by whichever gateway runs its worker's stream."""

import asyncio
import json

from pinecall.domain.agent import AgentConfig
from pinecall.gateway._gateway import Gateway
from pinecall.gateway._served import served_call
from pinecall.gateway.calls.commands import commanded
from pinecall.wire.frames import Command
from pinecall.wire.rest.calls import OpenCallRequest
from tests.conftest import AGENT as THE_KNOCKED_AGENT
from tests.conftest import Knocking, postgres, received_until, sent
from tests.fleet.test_client import a_call as a_widget_call
from tests.gateway.api.conftest import an_app, first_data
from tests.gateway.conftest import AGENT, OURS, a_call


@postgres
async def test_a_command_is_held_for_the_worker_of_its_agents_call_only(wired: Gateway) -> None:
    context = a_call()
    served = served_call(wired.serving, None, context, AgentConfig(slug=AGENT), OURS)
    await wired.live.commands_heard(context.call)
    signal = wired.connections.signal
    other = Command(type="agent.say", agent="otra", call=context.call, data={"text": "no"})
    text = Command(type="agent.say", agent=AGENT, call=context.call, data={"text": "hola"})
    assert await commanded(signal, other)
    assert await commanded(signal, text)
    assert await asyncio.wait_for(served.commands.get(), 1) == text
    nobodys = Command(type="agent.say", agent=AGENT, call="CA_nobody", data={"text": "hola"})
    assert not await commanded(signal, nobodys)


# The app is on one gateway, the worker streams its commands from the other.
@postgres
async def test_an_apps_command_reaches_a_worker_streaming_from_another_gateway(
    knocking: Knocking, knocking_two: Knocking
) -> None:
    app = await an_app(knocking)
    context = a_widget_call(knocking)
    opening = OpenCallRequest(agent=THE_KNOCKED_AGENT, context=context).written()
    async with (
        knocking.http(knocking.fleet["sandbox"]) as first,
        knocking_two.http(knocking.fleet["sandbox"]) as second,
    ):
        assert (await first.post("/v1/calls", json=opening)).is_success
        await received_until(app, "call.ringing")
        async with second.stream(
            "GET", f"/v1/calls/{context.call}/commands", headers={"Accept": "text/event-stream"}
        ) as stream:
            await sent(app, "agent.say", {"text": "hola"}, call=context.call)
            data = await asyncio.wait_for(first_data(stream.aiter_lines()), 5)
    assert json.loads(data)["type"] == "agent.say"
    await app.close()
