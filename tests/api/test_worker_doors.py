"""The five doors the worker knocks on, driven by worker/client.py over the real ASGI app."""

from __future__ import annotations

import asyncio

import pytest

from pinecall.api._live import Live
from pinecall.api.agents.registry import Registry
from pinecall.log.store import MemoryStore
from pinecall.log.writers import Logs
from pinecall.types import PRODUCTION, CallContext, Route
from pinecall.worker.client import Gateway, GatewayRefused
from pinecall_protocol import defs
from pinecall_protocol.events import ToolCall
from tests.api.conftest import A_RECORD, AGENT
from tests.api.talking import a_context as a_call_on

pytestmark = pytest.mark.unit

CALL = "call_from_the_worker"
AN_OWNER = "app_the_worker_doors"  # the id an app socket would have been minted
A_TOOL = defs.ToolSpec(
    name="find_slots", description="Free slots", parameters={"type": "object"}, timeout_s=0.2
)

# The clinic's own layout: the one static block it writes, and a dynamic one its agenda feeds.
A_LAYOUT = [
    defs.PromptBlockSpec(name="identity", region="static"),
    defs.PromptBlockSpec(name="availability", region="dynamic"),
]


async def declared(registry: Registry) -> None:
    """The clinic, registered and configured the way its app socket would have done it."""
    await registry.register(
        AN_OWNER, A_RECORD.org, PRODUCTION, AGENT, [defs.Route(channel="web", number=None)]
    )
    await registry.configure(
        AN_OWNER, PRODUCTION, AGENT, defs.AgentConfig(prompt=A_LAYOUT, tools=[A_TOOL])
    )


def a_context(org: str = A_RECORD.org) -> CallContext:
    return a_call_on(CALL, org)


async def test_the_routes_are_the_fleets_own_doors_as_the_domain_holds_them(
    worker_gateway: Gateway, registry: Registry
) -> None:
    await declared(registry)
    assert await worker_gateway.routes() == (Route(org="clinica", agent=AGENT, channel="web"),)


async def test_an_agents_config_comes_back_whole_and_an_unknown_one_is_a_refusal(
    worker_gateway: Gateway, registry: Registry
) -> None:
    await declared(registry)
    config = await worker_gateway.agent(AGENT)
    assert config.slug == AGENT
    assert [block.name for block in config.prompt] == ["identity", "availability"]
    assert [tool.name for tool in config.tools] == ["find_slots"]
    with pytest.raises(GatewayRefused, match="404"):
        await worker_gateway.agent("nobody")


async def test_opening_a_call_writes_its_first_entry_and_the_worker_appends_behind_it(
    worker_gateway: Gateway, registry: Registry, store: MemoryStore
) -> None:
    await declared(registry)
    await worker_gateway.opened(a_context(), AGENT)
    await worker_gateway.append(CALL, "agent.state", {"state": "thinking"}, True)
    written = await store.since(CALL)
    assert [entry.type for entry in written] == ["call.ringing", "agent.state"]
    assert written[0].data["from"] == "visitor_1"
    assert written[0].data["route"] == {"channel": "web", "number": None}
    assert (written[1].ephemeral, written[1].seq) == (True, 2)


async def test_a_call_of_another_fleet_is_refused_at_the_door(worker_gateway: Gateway) -> None:
    with pytest.raises(GatewayRefused, match="403"):
        await worker_gateway.opened(a_context(org="somebody-else"), AGENT)


async def test_an_entry_the_protocol_does_not_name_is_refused(
    worker_gateway: Gateway, registry: Registry
) -> None:
    await declared(registry)
    await worker_gateway.opened(a_context(), AGENT)
    with pytest.raises(GatewayRefused, match="no event is called 'bot.hummed'"):
        await worker_gateway.append(CALL, "bot.hummed", {})


async def test_an_entry_of_a_call_nobody_opened_here_is_refused_by_name(
    worker_gateway: Gateway,
) -> None:
    with pytest.raises(GatewayRefused, match="open it with POST /v1/calls first"):
        await worker_gateway.append("call_nobody_opened", "agent.state", {"state": "idle"})


async def test_sealing_ends_the_log_and_nothing_more_can_be_appended(
    worker_gateway: Gateway, registry: Registry, logs: Logs
) -> None:
    await declared(registry)
    await worker_gateway.opened(a_context(), AGENT)
    await worker_gateway.sealed(CALL)
    assert logs.opened(CALL) is None
    with pytest.raises(GatewayRefused, match="404"):
        await worker_gateway.append(CALL, "agent.state", {"state": "idle"})


async def test_a_tool_goes_out_as_tool_call_and_the_apps_answer_comes_back_as_tool_result(
    worker_gateway: Gateway, registry: Registry, store: MemoryStore, live: Live
) -> None:
    """Criterion 4's sixth door: the round trip the bridge's tools make, both entries logged."""
    await declared(registry)
    await worker_gateway.opened(a_context(), AGENT)
    wanted = ToolCall(
        call_id="tu_1", name="find_slots", arguments={"day": "martes"}, speech_id="sp_1"
    )
    asking = asyncio.create_task(
        worker_gateway.tool(CALL, AGENT, wanted, timeout_s=A_TOOL.timeout_s or 1)
    )
    await _until(store, "tool.call")
    assert live.answered(CALL, defs.ToolResult(call_id="tu_1", name="find_slots", output="10:15"))
    result = await asking
    assert (result.call_id, result.output) == ("tu_1", "10:15")
    types = [entry.type for entry in await store.since(CALL)]
    assert types == ["call.ringing", "tool.call", "tool.result"]


async def test_a_tool_that_returned_null_is_logged_with_its_null_and_not_without_an_output(
    worker_gateway: Gateway, registry: Registry, store: MemoryStore, live: Live
) -> None:
    """A method that found nobody answers null; the log keeps it, distinct from no answer at all."""
    await declared(registry)
    await worker_gateway.opened(a_context(), AGENT)
    wanted = ToolCall(call_id="tu_4", name="find_slots", arguments={"day": "domingo"})
    asking = asyncio.create_task(worker_gateway.tool(CALL, AGENT, wanted, timeout_s=1))
    await _until(store, "tool.call")
    assert live.answered(CALL, defs.ToolResult(call_id="tu_4", name="find_slots", output=None))
    await asking
    logged = [entry for entry in await store.since(CALL) if entry.type == "tool.result"]
    assert "output" in logged[0].data and logged[0].data["output"] is None


async def test_a_tool_nobody_answers_lapses_at_its_own_deadline_and_says_so(
    worker_gateway: Gateway, registry: Registry
) -> None:
    await declared(registry)
    await worker_gateway.opened(a_context(), AGENT)
    wanted = ToolCall(call_id="tu_2", name="find_slots", arguments={})
    result = await worker_gateway.tool(CALL, AGENT, wanted, timeout_s=0.2)
    assert result.error is not None and "did not answer within 0.2s" in result.error


async def test_a_tool_of_an_agent_no_app_holds_is_a_refusal_not_a_wait(
    worker_gateway: Gateway,
) -> None:
    wanted = ToolCall(call_id="tu_3", name="find_slots", arguments={})
    with pytest.raises(GatewayRefused, match="409"):
        await worker_gateway.tool(CALL, AGENT, wanted, timeout_s=1)


async def test_an_answer_nobody_waits_for_is_told_so(live: Live) -> None:
    assert (
        live.answered(CALL, defs.ToolResult(call_id="tu_9", name="find_slots", output="x")) is False
    )


async def _until(store: MemoryStore, type: str) -> None:
    """Wait for one entry type to be written, so the test answers the tool the log has asked for."""
    for _ in range(100):
        if any(entry.type == type for entry in await store.since(CALL)):
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"{type} was never written")
