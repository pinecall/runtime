"""Tests for the callbacks the overflow takes."""

from tests.conftest import (
    AGENT,
    Knocking,
    postgres,
)
from tests.gateway.api.conftest import an_app


@postgres
async def test_a_callback_the_overflow_took_is_listed_for_the_org(knocking: Knocking) -> None:
    socket = await an_app(knocking)
    wanted = {"agent": AGENT, "channel": "phone", "number": "+59899000002", "call": None}
    async with knocking.http(knocking.fleet["sandbox"]) as overflow:
        taken = await overflow.post("/v1/callbacks", json=wanted)
    async with knocking.http(knocking.app["sandbox"]) as tenant:
        listed = (await tenant.get("/v1/callbacks")).json()
    assert taken.status_code == 204
    assert [item["number"] for item in listed["requests"]] == ["+59899000002"]
    await socket.close()
