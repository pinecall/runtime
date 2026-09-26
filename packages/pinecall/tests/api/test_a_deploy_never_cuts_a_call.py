"""A deploy never cuts a call: the gateway forgets it, the app's process changes, it goes on."""

from __future__ import annotations

import asyncio

import pytest

from pinecall.live.attaching import handed_on, parked_calls_of
from pinecall.live.calls import Live
from pinecall.live.registry import Registry
from pinecall.log.entry import Entry
from pinecall.log.store import MemoryStore
from pinecall.log.writers import Logs
from pinecall.types import PRODUCTION
from pinecall.worker.gateway_client import Gateway
from pinecall_protocol import defs
from pinecall_protocol.events import ToolCall
from tests.api.conftest import A_RECORD, AGENT
from tests.api.talking import collecting, until
from tests.api.test_served_call import A_STARTED
from tests.api.test_worker_doors import AN_OWNER, CALL, a_context, declared

pytestmark = pytest.mark.unit

THE_NEW_PROCESS = "app_the_new_process"


def answered(live: Live, output: str) -> bool:
    """The app's tool.result for the one tool the test asks."""
    return live.answered(CALL, defs.ToolResult(call_id="tu_1", name="find_slots", output=output))


async def test_a_gateway_that_forgot_the_call_mid_call_takes_the_workers_append_and_tool(
    worker_gateway: Gateway, registry: Registry, live: Live, logs: Logs, store: MemoryStore
) -> None:
    await declared(registry)
    heard: list[Entry] = []
    live.connect(AN_OWNER, collecting(heard))
    await worker_gateway.opened(a_context(), AGENT)
    live.close(CALL)
    logs.forget(CALL)
    await worker_gateway.append(CALL, "call.started", A_STARTED)
    wanted = ToolCall(call_id="tu_1", name="find_slots", arguments={"day": "martes"})
    asking = asyncio.ensure_future(worker_gateway.tool(CALL, AGENT, wanted, timeout_s=1))
    await until(heard, "tool.call")
    assert answered(live, "10:15")
    assert (await asking).output == "10:15"
    written = [entry.type for entry in await store.since(CALL)]
    assert written == ["call.ringing", "call.attached", "call.started", "tool.call", "tool.result"]


async def test_a_process_that_left_with_a_tool_waiting_is_replaced_and_the_tool_answered(
    worker_gateway: Gateway, registry: Registry, live: Live
) -> None:
    await declared(registry)
    before: list[Entry] = []
    live.connect(AN_OWNER, collecting(before))
    await worker_gateway.opened(a_context(), AGENT)
    wanted = ToolCall(call_id="tu_1", name="find_slots", arguments={"day": "martes"})
    asking = asyncio.ensure_future(worker_gateway.tool(CALL, AGENT, wanted, timeout_s=2))
    await until(before, "tool.call")
    # The old process goes, as the socket's own close does it: parked, released, handed on.
    live.disconnect(AN_OWNER)
    parked = live.park(AN_OWNER)
    await registry.release(AN_OWNER)
    assert await handed_on(live, registry, parked) == (0, 1)
    # The new one registers the agent, and the call is its.
    after: list[Entry] = []
    live.connect(THE_NEW_PROCESS, collecting(after))
    await registry.register(THE_NEW_PROCESS, A_RECORD.org, PRODUCTION, AGENT)
    await parked_calls_of(live, (PRODUCTION, None, AGENT), THE_NEW_PROCESS)
    await until(after, "tool.call")
    assert [entry.type for entry in after] == ["call.attached", "tool.call"]
    assert answered(live, "10:15")
    assert (await asking).output == "10:15"
