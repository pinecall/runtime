"""A call handed to a socket: call.attached with its state, and the socket that left handed on."""

import asyncio

from pinecall.domain.agent import AgentConfig
from pinecall.gateway._gateway import Gateway
from pinecall.gateway._served import served_call
from pinecall.gateway.calls.binding import attach, handed_on
from pinecall.wire.frames import Entry
from tests.conftest import postgres
from tests.gateway.conftest import AGENT, OURS, a_call, a_start
from tests.gateway.test_served import holding


@postgres
async def test_a_call_served_to_a_socket_reaches_it_and_moves_when_the_socket_leaves(
    wired: Gateway,
) -> None:
    await holding(wired.sockets, "app_1", OURS)
    await holding(wired.sockets, "app_2", OURS)
    got: list[Entry] = []

    async def into_the_socket(entry: Entry) -> None:
        got.append(entry)

    wired.live.sockets["app_1"] = into_the_socket
    wired.live.sockets["app_2"] = into_the_socket
    context = a_call()
    served = served_call(wired.serving, "app_2", context, AgentConfig(slug=AGENT), OURS)
    await served.log.append("call.started", a_start(context))
    await served.log.append("custom", {"name": "x", "data": {}})
    await asyncio.sleep(0.05)
    assert [item.type for item in got] == ["call.started", "custom"]
    await wired.sockets.release("app_2")
    handed, parked = await handed_on(wired.live, wired.sockets, ["call_nobody", context.call])
    assert (handed, parked) == (1, 0)
    await asyncio.sleep(0.05)
    assert got[-1].type == "call.attached"
    assert wired.live.calls[context.call].app == "app_1"


@postgres
async def test_attaching_names_the_start_the_state_and_the_seq(wired: Gateway) -> None:
    context = a_call()
    served = served_call(wired.serving, None, context, AgentConfig(slug=AGENT), OURS)
    await served.log.append("call.started", a_start(context))
    await served.log.append("state.changed", {"state": {"step": 2}, "changed": ["step"]})
    entry = await attach(wired.live, context.call, "app_9")
    assert entry is not None
    assert entry.data["state"] == {"step": 2}
    assert entry.data["seq"] == 2
    assert await attach(wired.live, context.call, "app_9") is None
