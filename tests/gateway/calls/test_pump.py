"""The pump: a bound call's entries reach its socket whichever gateway wrote them, tools too."""

import asyncio

from pinecall.domain.names import JsonObject
from pinecall.domain.scope import Scope
from pinecall.gateway._sockets import HELD_CHANNEL
from pinecall.wire.rest.calls import OpenCallRequest
from tests.conftest import AGENT as THE_KNOCKED_AGENT
from tests.conftest import Knocking, postgres, received_until, sent
from tests.fleet.test_client import a_call as a_widget_call
from tests.gateway.api.conftest import an_app


async def held_on(second: Knocking) -> None:
    """Wait until the other gateway heard which app holds the agent in the sandbox."""
    sockets, own = second.gateway.sockets, Scope(second.org.id, "sandbox")
    shares = await second.gateway.connections.signal.subscribe(HELD_CHANNEL)
    await asyncio.sleep(0)
    try:
        async with asyncio.timeout(2):
            while sockets.of(own, THE_KNOCKED_AGENT) is None:
                await anext(shares)
                await asyncio.sleep(0)
    finally:
        shares.close()


# The worker asks the tool of one gateway; the app that answers it is on the other. The
# call.ringing, the tool.call and the tool.result travel through the log, each written once.
@postgres
async def test_a_tool_asked_on_one_gateway_is_answered_by_the_app_on_the_other(
    knocking: Knocking, knocking_two: Knocking
) -> None:
    app = await an_app(knocking)
    await held_on(knocking_two)
    context = a_widget_call(knocking)
    opening = OpenCallRequest(agent=THE_KNOCKED_AGENT, context=context).written()
    async with knocking_two.http(knocking.fleet["sandbox"]) as worker:
        assert (await worker.post("/v1/calls", json=opening)).is_success
        assert (await received_until(app, "call.ringing")).call == context.call
        asking = asyncio.create_task(
            worker.post(
                f"/v1/calls/{context.call}/tools?agent={THE_KNOCKED_AGENT}",
                json={"call_id": "c1", "name": "book", "arguments": {"day": "monday"}},
            )
        )
        called = await received_until(app, "tool.call")
        assert called.data["arguments"] == {"day": "monday"}
        answer: JsonObject = {"call_id": "c1", "name": "book", "output": {"booked": True}}
        await sent(app, "tool.result", answer, call=context.call)
        result = await asyncio.wait_for(asking, 5)
    assert result.json()["output"] == {"booked": True}
    kinds = [entry.type for entry in await knocking.gateway.logs.store.whole(context.call)]
    assert (kinds.count("tool.call"), kinds.count("tool.result")) == (1, 1)
    await app.close()


# The retry of a tool whose gateway died reaches the other: it waits for the same tool.call's
# answer, and the app is not asked twice.
@postgres
async def test_a_tool_retried_on_another_gateway_is_waited_on_and_never_asked_twice(
    knocking: Knocking, knocking_two: Knocking
) -> None:
    app = await an_app(knocking)
    await held_on(knocking_two)
    context = a_widget_call(knocking)
    opening = OpenCallRequest(agent=THE_KNOCKED_AGENT, context=context).written()
    tool: JsonObject = {"call_id": "c1", "name": "book", "arguments": {}}
    path = f"/v1/calls/{context.call}/tools?agent={THE_KNOCKED_AGENT}"
    async with (
        knocking.http(knocking.fleet["sandbox"]) as first,
        knocking_two.http(knocking.fleet["sandbox"]) as second,
    ):
        assert (await first.post("/v1/calls", json=opening)).is_success
        first_try = asyncio.create_task(first.post(path, json=tool))
        await received_until(app, "tool.call")
        first_try.cancel()
        retried = asyncio.create_task(second.post(path, json=tool))
        await asyncio.sleep(0.2)
        answer: JsonObject = {"call_id": "c1", "name": "book", "output": "ok"}
        await sent(app, "tool.result", answer, call=context.call)
        result = await asyncio.wait_for(retried, 5)
    assert result.json()["output"] == "ok"
    kinds = [entry.type for entry in await knocking.gateway.logs.store.whole(context.call)]
    assert (kinds.count("tool.call"), kinds.count("tool.result")) == (1, 1)
    await app.close()
