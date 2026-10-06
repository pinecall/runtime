"""Tests for the lists of calls: the org's newest, one row each."""

from pinecall.wire.rest.calls import OpenCallRequest
from tests.conftest import AGENT, Knocking, postgres
from tests.gateway.api.conftest import a_call


@postgres
async def test_the_org_lists_its_calls_newest_first(knocking: Knocking) -> None:
    calls = [a_call(knocking), a_call(knocking)]
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        for context in calls:
            await worker.post(
                "/v1/calls", json=OpenCallRequest(agent=AGENT, context=context).written()
            )
    async with knocking.http(knocking.app["sandbox"]) as tenant:
        listed = (await tenant.get("/v1/sessions")).json()
    assert [line["call"] for line in listed["calls"]] == [call.call for call in reversed(calls)]
    assert all(line["live"] for line in listed["calls"])
