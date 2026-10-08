"""Tests for the callbacks the overflow takes."""

from pinecall.wire.rest.calls import OpenCallRequest
from tests.conftest import (
    AGENT,
    Knocking,
    postgres,
)
from tests.gateway.api.conftest import a_call, an_app


@postgres
async def test_a_callback_the_overflow_took_is_listed_for_the_org(knocking: Knocking) -> None:
    socket = await an_app(knocking)
    context = a_call(knocking)
    wanted = {"agent": AGENT, "channel": "phone", "number": "+59899000002", "call": context.call}
    async with knocking.http(knocking.fleet["sandbox"]) as overflow:
        await overflow.post(
            "/v1/calls", json=OpenCallRequest(agent=AGENT, context=context).written()
        )
        taken = await overflow.post("/v1/callbacks", json=wanted)
    async with knocking.http(knocking.app["sandbox"]) as tenant:
        listed = (await tenant.get("/v1/callbacks")).json()
    assert taken.status_code == 204
    assert [item["number"] for item in listed["requests"]] == ["+59899000002"]
    await socket.close()


# One agent's log holds both worlds' callbacks; each world lists only its own calls'.
@postgres
async def test_a_sandbox_callback_is_not_listed_in_production(knocking: Knocking) -> None:
    socket = await an_app(knocking)
    context = a_call(knocking)
    wanted = {"agent": AGENT, "channel": "phone", "number": "+59899000003", "call": context.call}
    async with knocking.http(knocking.fleet["sandbox"]) as overflow:
        await overflow.post(
            "/v1/calls", json=OpenCallRequest(agent=AGENT, context=context).written()
        )
        await overflow.post("/v1/callbacks", json=wanted)
    async with knocking.http(knocking.app["production"]) as production:
        listed = (await production.get("/v1/callbacks")).json()
    assert listed["requests"] == []
    await socket.close()


# The overflow answered a call: the callback is that call's org's, and names it.
@postgres
async def test_the_overflow_asks_a_call_back_only_for_a_call_it_opened(knocking: Knocking) -> None:
    socket = await an_app(knocking)
    wanted = {"agent": AGENT, "channel": "phone", "number": "+59899000002"}
    async with knocking.http(knocking.fleet["sandbox"]) as overflow:
        unnamed = await overflow.post("/v1/callbacks", json={**wanted, "call": None})
        unopened = await overflow.post("/v1/callbacks", json={**wanted, "call": "call_nobody"})
    assert unnamed.status_code == 400
    assert "name the call" in unnamed.json()["detail"]
    assert unopened.status_code == 404
    await socket.close()
