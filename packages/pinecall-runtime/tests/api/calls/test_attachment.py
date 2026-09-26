"""A live call handed to another socket: call.attached first, then the tools still waiting."""

from __future__ import annotations

import asyncio

import pytest

from pinecall.api.calls.attachment import attach_socket
from pinecall.live.calls import Live
from pinecall.live.registry import Registry
from pinecall.log.entry import Entry
from pinecall.worker.gateway_client import Gateway
from pinecall_protocol import defs
from pinecall_protocol.events import ToolCall
from tests.api.conftest import AGENT
from tests.api.talking import collecting, until
from tests.api.test_served_call import A_STARTED
from tests.api.test_worker_doors import AN_OWNER, CALL, a_context, declared

pytestmark = pytest.mark.unit

THE_NEXT = "app_the_next_process"


async def a_call_on_the_first_socket(
    worker_gateway: Gateway, registry: Registry, live: Live
) -> list[Entry]:
    """The clinic's call, opened by the worker and served by the socket that held the agent."""
    await declared(registry)
    heard: list[Entry] = []
    live.connect(AN_OWNER, collecting(heard))
    await worker_gateway.opened(a_context(), AGENT)
    return heard


async def test_attaching_writes_call_attached_with_the_start_the_last_state_and_the_seq(
    worker_gateway: Gateway, registry: Registry, live: Live
) -> None:
    await a_call_on_the_first_socket(worker_gateway, registry, live)
    await worker_gateway.append(CALL, "call.started", A_STARTED)
    for state in ({"patient": None}, {"patient": "p1"}):
        await worker_gateway.append(CALL, "state.changed", {"state": state, "changed": ["patient"]})
    heard: list[Entry] = []
    live.connect(THE_NEXT, collecting(heard))
    said = await attach_socket(live, CALL, THE_NEXT)
    assert said is not None and said.type == "call.attached"
    assert said.data["app"] == THE_NEXT
    assert said.data["started"]["from"] == A_STARTED["from"]
    assert said.data["state"] == {"patient": "p1"}
    assert said.data["seq"] == said.seq - 1
    assert said.data["claimed"] is None, "no page followed this call"
    await until(heard, "call.attached")
    assert live.app_of(CALL) == THE_NEXT


async def test_a_call_that_claimed_a_code_is_handed_on_still_claimed(
    worker_gateway: Gateway, registry: Registry, live: Live
) -> None:
    """The next socket's view asks whether the caller is on the site, and gets the same answer."""
    await a_call_on_the_first_socket(worker_gateway, registry, live)
    await worker_gateway.append(CALL, "call.started", A_STARTED)
    await worker_gateway.append(CALL, "call.claimed", {"code": "0427", "via": "keypad"})
    live.connect(THE_NEXT, collecting([]))
    said = await attach_socket(live, CALL, THE_NEXT)
    assert said is not None and said.data["claimed"] == "0427"


async def test_the_tools_still_waiting_are_re_sent_to_the_new_socket_with_their_seq(
    worker_gateway: Gateway, registry: Registry, live: Live
) -> None:
    first = await a_call_on_the_first_socket(worker_gateway, registry, live)
    wanted = ToolCall(call_id="tu_1", name="find_slots", arguments={"day": "martes"})
    asking = asyncio.ensure_future(worker_gateway.tool(CALL, AGENT, wanted, timeout_s=1))
    await until(first, "tool.call")
    heard: list[Entry] = []
    live.connect(THE_NEXT, collecting(heard))
    await attach_socket(live, CALL, THE_NEXT)
    await until(heard, "tool.call")
    assert [entry.type for entry in heard] == ["call.attached", "tool.call"]
    assert heard[1].seq == next(entry.seq for entry in first if entry.type == "tool.call")
    assert live.answered(CALL, defs.ToolResult(call_id="tu_1", name="find_slots", output="10:15"))
    assert (await asking).output == "10:15"


async def test_a_socket_that_already_serves_the_call_is_not_attached_again(
    worker_gateway: Gateway, registry: Registry, live: Live
) -> None:
    await a_call_on_the_first_socket(worker_gateway, registry, live)
    assert await attach_socket(live, CALL, AN_OWNER) is None
