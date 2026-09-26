"""A call the gateway forgot — it restarted — is told again by the worker that still holds it."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from starlette.testclient import TestClient

from pinecall.api.agents.registry import Registry
from pinecall.api.live import Live
from pinecall.log.entry import Entry
from pinecall.log.store import MemoryStore
from pinecall.log.writers import Logs
from pinecall.worker.gateway_client import CONTEXT, Gateway
from tests.api.conftest import A_KEY, AGENT
from tests.api.talking import collecting, until
from tests.api.test_served_call import A_STARTED
from tests.api.test_worker_doors import AN_OWNER, CALL, a_context, declared

pytestmark = pytest.mark.unit


def forgotten(live: Live, logs: Logs) -> None:
    """What a restart takes from the gateway: the call it was serving and the log it was writing."""
    live.close(CALL)
    logs.forget(CALL)


def reopening(client: TestClient) -> int:
    """The worker saying it again, straight at the door, as the status it answered with."""
    said = {"agent": AGENT, "context": CONTEXT.dump_python(a_context(), mode="json")}
    handle: Any = client
    answer: Any = handle.post(
        f"/v1/calls/{CALL}/reopened", json=said, headers={"Authorization": f"Bearer {A_KEY}"}
    )
    status: int = answer.status_code
    return status


async def test_an_append_to_a_call_the_gateway_forgot_reopens_it_and_lands(
    worker_gateway: Gateway, live: Live, logs: Logs, store: MemoryStore
) -> None:
    await worker_gateway.opened(a_context(), AGENT)
    forgotten(live, logs)
    await worker_gateway.append(CALL, "call.started", A_STARTED)
    assert live.served(CALL) is not None
    assert [entry.type for entry in await store.since(CALL)] == ["call.ringing", "call.started"]


async def test_a_reopened_call_is_attached_to_the_socket_holding_its_agent(
    worker_gateway: Gateway, registry: Registry, live: Live, logs: Logs
) -> None:
    await declared(registry)
    heard: list[Entry] = []
    live.connect(AN_OWNER, collecting(heard))
    await worker_gateway.opened(a_context(), AGENT)
    forgotten(live, logs)
    await worker_gateway.append(CALL, "call.started", A_STARTED)
    await until(heard, "call.started")
    assert live.app_of(CALL) == AN_OWNER
    assert [entry.type for entry in heard][-2:] == ["call.attached", "call.started"]


async def test_the_command_stream_of_a_call_the_gateway_forgot_is_reopened() -> None:
    asked: list[str] = []
    frame = 'data: {"type":"agent.say","agent":"%s","call":"%s","data":{"text":"Un momento"}}\n\n'

    def answering(request: httpx.Request) -> httpx.Response:
        asked.append(f"{request.method} {request.url.path}")
        if request.url.path == f"/v1/calls/{CALL}/commands" and len(asked) == 2:
            return httpx.Response(404, json={"detail": "not served"})
        if request.url.path == f"/v1/calls/{CALL}/commands":
            return httpx.Response(200, text=frame % (AGENT, CALL))
        return httpx.Response(204)

    http = httpx.AsyncClient(transport=httpx.MockTransport(answering), base_url="http://gw.test")
    worker = Gateway(http)
    await worker.opened(a_context(), AGENT)
    said = [command async for command in worker.commands(CALL)]
    assert [command.type for command in said] == ["agent.say"]
    assert asked == [
        "POST /v1/calls",
        f"GET /v1/calls/{CALL}/commands",
        f"POST /v1/calls/{CALL}/reopened",
        f"GET /v1/calls/{CALL}/commands",
    ]


def test_a_call_nobody_opened_is_not_reopened(gateway: TestClient) -> None:
    assert reopening(gateway) == 404


async def test_a_sealed_call_is_not_reopened(worker_gateway: Gateway, gateway: TestClient) -> None:
    await worker_gateway.opened(a_context(), AGENT)
    await worker_gateway.sealed(CALL)
    assert reopening(gateway) == 409
