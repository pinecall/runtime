"""A call a gateway learns of from another: served from its opening, or not at all."""

from pinecall.gateway.calls.known import known_here
from pinecall.wire.rest.calls import OpenCallRequest
from tests.conftest import AGENT, Knocking, postgres
from tests.fleet.test_client import a_call


@postgres
async def test_a_call_opened_elsewhere_is_served_here_from_its_opening_and_nobodys_is_not(
    knocking: Knocking, knocking_two: Knocking
) -> None:
    context = a_call(knocking)
    opening = OpenCallRequest(agent=AGENT, context=context).written()
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        assert (await worker.post("/v1/calls", json=opening)).is_success
    served = await known_here(knocking_two.gateway.serving, context.call)
    assert served is not None
    assert (served.context, served.opened_here, served.app) == (context, False, None)
    assert await known_here(knocking_two.gateway.serving, context.call) is served
    assert await known_here(knocking_two.gateway.serving, "CA_nobody") is None
    await knocking.gateway.logs.store.seal(context.call)
    knocking_two.gateway.live.close(context.call)
    assert await known_here(knocking_two.gateway.serving, context.call) is None
