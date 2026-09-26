"""A call the worker opened and the app serves: its entries down the socket, its commands back."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable
from typing import Any

import httpx
import pytest
from fastapi import HTTPException
from starlette.testclient import TestClient

from pinecall.api.agents.registry import Registry
from pinecall.api.calls import commands as door
from pinecall.api.live import Live
from pinecall.log.entry import Entry
from pinecall.log.writers import Logs
from pinecall.types import AgentConfig
from pinecall.worker import commanding
from pinecall.worker.client import CONTEXT, Gateway
from pinecall.worker.hop import GatewayRefused
from pinecall_protocol import Command, defs
from pinecall_protocol.events import ToolCall
from tests.api.conftest import A_KEY, A_RECORD, AGENT
from tests.api.talking import a_context as a_call_on
from tests.api.talking import a_frame, an_app, collecting, declared, until
from tests.api.test_worker_doors import AN_OWNER, CALL, a_context
from tests.api.test_worker_doors import declared as registered

pytestmark = pytest.mark.unit

A_STARTED: dict[str, Any] = {
    "channel": "web",
    "direction": "inbound",
    "from": "visitor_1",
    "to": AGENT,
    "caller": None,
    "started_at": 1_757_000_000.0,
}


class Applied:
    """The bridge as the command loop reaches it: what it was asked to apply, in order."""

    def __init__(self) -> None:
        self.applied: list[Command] = []

    async def apply(self, command: Command) -> None:
        """One protocol command onto this call."""
        self.applied.append(command)


# Criterion 1. The app never asked for this call and never opened it: it hears of it because the
# worker opened its log here, and this gateway put that log on the socket holding the agent.
def test_a_call_the_worker_opened_arrives_on_the_app_socket_that_holds_its_agent(
    gateway: TestClient,
) -> None:
    with an_app(gateway) as app_socket:
        declared(app_socket)
        assert _posted(gateway, "/v1/calls", _an_opening()) == 200
        assert _posted(gateway, f"/v1/calls/{CALL}/events", _an_entry("call.started")) == 204
        assert [_heard(app_socket), _heard(app_socket)] == ["call.ringing", "call.started"]


# Criterion 1's other half: a tool of that call goes out down the very socket the entries arrive
# on, and the app's answer comes back to the worker that asked. Before this card the app had no
# instance for the call, so every one of these answered "this call is no longer being served".
async def test_a_tool_of_that_call_travels_down_the_same_socket_the_entries_arrive_on(
    worker_gateway: Gateway, registry: Registry, live: Live
) -> None:
    await registered(registry)
    heard: list[Entry] = []
    live.connect(AN_OWNER, collecting(heard))
    await worker_gateway.opened(a_context(), AGENT)
    wanted = ToolCall(call_id="tu_1", name="find_slots", arguments={"day": "martes"})
    asking = asyncio.ensure_future(worker_gateway.tool(CALL, AGENT, wanted, timeout_s=1))
    await until(heard, "tool.call")
    assert live.answered(CALL, defs.ToolResult(call_id="tu_1", name="find_slots", output="10:15"))
    assert (await asking).output == "10:15"
    assert [entry.type for entry in heard] == ["call.ringing", "tool.call", "tool.result"]


# The id of a call is not a key to it. A call served under the shop — the fleet's worker opened
# it, say — answers the clinic's worker at neither door: a tool of it would write into the shop's
# log, and reading its commands would CONSUME them, off the worker that is running it.
async def test_another_orgs_served_call_opens_neither_its_tools_nor_its_commands(
    worker_gateway: Gateway, registry: Registry, live: Live, logs: Logs
) -> None:
    await registered(registry)
    theirs = "call_of_the_shop"
    live.serve(
        theirs,
        AGENT,
        "tienda",
        logs.writing(theirs, AGENT),
        None,
        context=a_call_on(theirs, "tienda"),
        config=AgentConfig(slug=AGENT),
    )
    wanted = ToolCall(call_id="tu_2", name="find_slots", arguments={})
    with pytest.raises(GatewayRefused, match="403"):
        await worker_gateway.tool(theirs, AGENT, wanted, timeout_s=1)
    with pytest.raises(HTTPException) as refused:
        await door.commands(theirs, A_RECORD, live)
    assert refused.value.status_code == 403
    assert live.commands(theirs) is not None


# Criterion 3, from the app's side: the same registration ends when the call is sealed, and an
# entry of a call nobody serves any more reaches nobody.
def test_a_sealed_call_leaves_the_socket_and_a_command_for_it_is_refused_by_name(
    gateway: TestClient, live: Live
) -> None:
    with an_app(gateway) as app_socket:
        declared(app_socket)
        _posted(gateway, "/v1/calls", _an_opening())
        assert _heard(app_socket) == "call.ringing"
        assert live.commands(CALL) is not None
        _posted(gateway, f"/v1/calls/{CALL}/sealed", None)
        assert live.commands(CALL) is None
        app_socket.send_json(_a_prompt())
        assert app_socket.receive_json()["data"]["code"] == "no_session"


# Criterion 2, the app's half: a command for a call this process does not run itself is not
# refused any more — it waits for the worker that does run it.
def test_a_command_for_a_worker_run_call_is_held_for_the_worker_and_never_refused(
    gateway: TestClient, live: Live
) -> None:
    with an_app(gateway) as app_socket:
        declared(app_socket)
        _posted(gateway, "/v1/calls", _an_opening())
        assert _heard(app_socket) == "call.ringing"
        app_socket.send_json(_a_prompt())
        # ping is the barrier: the socket answers its frames in order, so a pong back means the
        # prompt.set before it has already been taken.
        app_socket.send_json(a_frame("ping", AGENT))
        assert _heard(app_socket) == "pong"
        waiting = live.commands(CALL)
        assert waiting is not None
        held = waiting.get_nowait()
        assert held is not None and (held.type, held.data["name"]) == ("prompt.set", "view")


# Criterion 2, the worker's half. The gateway's own door writes the stream and the worker's own
# client reads it; the bytes are spliced by hand because httpx's ASGI transport buffers a response
# whole (httpx 0.28, _transports/asgi.py), so an endless SSE cannot be driven through it in a test.
async def test_what_the_app_said_reaches_the_bridge_of_the_worker_running_the_call(
    worker_gateway: Gateway, registry: Registry, live: Live
) -> None:
    await registered(registry)
    await worker_gateway.opened(a_context(), AGENT)
    streaming = await door.commands(CALL, A_RECORD, live)
    assert live.commanded(CALL, AGENT, _a_command("prompt.set", {"name": "view", "text": "Ana"}))
    assert live.commanded(CALL, AGENT, _a_command("call.hangup", {}))
    live.close(CALL)
    bridge = Applied()
    await commanding.served(_reading(await _drained(streaming.body_iterator)), bridge, CALL)
    assert [command.type for command in bridge.applied] == ["prompt.set", "call.hangup"]
    assert bridge.applied[0].data == {"name": "view", "text": "Ana"}


async def test_a_command_of_another_agent_is_not_held_for_this_calls_worker(
    worker_gateway: Gateway, registry: Registry, live: Live
) -> None:
    await registered(registry)
    await worker_gateway.opened(a_context(), AGENT)
    said = Command(type="call.hangup", agent="somebody-else", call=CALL, data={})
    assert live.commanded(CALL, "somebody-else", said) is False


async def test_a_worker_reading_the_commands_of_a_call_nobody_opened_here_is_refused(
    live: Live,
) -> None:
    with pytest.raises(HTTPException, match="open it with POST /v1/calls first"):
        await door.commands("call_nobody_opened", A_RECORD, live)


# ── what a test says to the doors ───────────────────────────────────────────────


def _an_opening() -> dict[str, Any]:
    """The body POST /v1/calls takes: whose agent it is, and everything the worker knows of it."""
    context = a_context()
    return {"agent": AGENT, "context": CONTEXT.dump_python(context, mode="json")}


def _an_entry(type: str) -> dict[str, Any]:
    """One entry as the worker hands it over, once its call is open."""
    return {"type": type, "data": A_STARTED}


def _a_prompt() -> dict[str, object]:
    """The frame an app sends to rewrite the view of a call it is serving."""
    return a_frame("prompt.set", AGENT, {"name": "view", "text": "Ana is calling"}, call=CALL)


def _a_command(type: str, data: dict[str, Any]) -> Command:
    """One command as the app socket hands it to the live memory."""
    return Command(type=type, agent=AGENT, call=CALL, data=data)


def _posted(client: TestClient, path: str, said: Any) -> int:
    """One POST at a worker's door, as the status it answered with."""
    handle: Any = client
    answer: Any = handle.post(path, json=said, headers={"Authorization": f"Bearer {A_KEY}"})
    status: int = answer.status_code
    return status


def _heard(app_socket: Any) -> str:
    """The type of the next entry the app's socket is handed."""
    frame: dict[str, Any] = app_socket.receive_json()
    return str(frame["type"])


async def _drained(body: AsyncIterable[Any]) -> str:
    """The whole body the door wrote, as the worker's client will read it off the wire."""
    parts = [chunk async for chunk in body]
    return "".join(part if isinstance(part, str) else part.decode() for part in parts)


def _reading(said: str) -> Gateway:
    """The worker's own client with that stream behind it, then a call that is over."""
    answers = iter([httpx.Response(200, text=said)])

    def answering(_request: httpx.Request) -> httpx.Response:
        return next(answers, httpx.Response(409, json={"detail": "over"}))

    transport = httpx.MockTransport(answering)
    return Gateway(httpx.AsyncClient(transport=transport, base_url="http://gateway.test"))
