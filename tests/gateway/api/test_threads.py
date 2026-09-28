"""Tests for an agent's WhatsApp inbox doors."""

from tests.conftest import Knocking, postgres
from tests.gateway.api.conftest import an_app


@postgres
async def test_an_agent_with_no_conversation_has_an_empty_inbox(knocking: Knocking) -> None:
    socket = await an_app(knocking)
    async with knocking.http(knocking.app["sandbox"]) as org:
        answer = await org.get("/v1/agents/front-desk/threads")
    await socket.close()
    assert answer.status_code == 200
    assert answer.json()["threads"] == []
