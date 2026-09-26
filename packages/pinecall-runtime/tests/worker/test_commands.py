"""The app's command stream, read by the worker: opened again when cut, ended when the call is."""

from __future__ import annotations

import httpx
import pytest

from pinecall.worker import commands, retries
from pinecall.worker.gateway_client import Gateway
from pinecall_protocol import Command

pytestmark = pytest.mark.unit

CALL = "call_1"
A_SAY = 'data: {"type":"agent.say","agent":"clinica","call":"call_1","data":{"text":"Hola"}}\n\n'


class Applied:
    """The bridge as the loop reaches it: what it was asked to apply."""

    def __init__(self) -> None:
        self.applied: list[Command] = []

    async def apply(self, command: Command) -> None:
        """One command onto the call."""
        self.applied.append(command)


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    """The backoff, taken down instead of slept."""

    async def slept(_seconds: float) -> None:
        return None

    monkeypatch.setattr(commands.asyncio, "sleep", slept)
    monkeypatch.setattr(retries.asyncio, "sleep", slept)


def a_gateway(*answers: httpx.Response | None) -> tuple[Gateway, list[str]]:
    """The worker's client over a gateway that answers these, in order; None is unreachable."""
    asked: list[str] = []
    queued = iter(answers)

    def answering(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        answer = next(queued)
        if answer is None:
            raise httpx.ConnectError("connection refused", request=request)
        return answer

    transport = httpx.MockTransport(answering)
    return Gateway(httpx.AsyncClient(transport=transport, base_url="http://gw.test")), asked


async def test_a_command_stream_that_ends_is_opened_again_until_the_gateway_refuses() -> None:
    """Cut, or ended cleanly by a gateway stopping with grace: both are the gateway going away."""
    over = httpx.Response(409, json={"detail": "over"})
    gateway, asked = a_gateway(None, httpx.Response(502), httpx.Response(200, text=A_SAY), over)
    bridge = Applied()
    await commands.serve_commands(gateway, bridge, CALL)
    assert [command.type for command in bridge.applied] == ["agent.say"]
    assert len(asked) == 4


async def test_a_command_stream_the_gateway_refuses_is_not_asked_again() -> None:
    gateway, asked = a_gateway(httpx.Response(409, json={"detail": "over"}))
    await commands.serve_commands(gateway, Applied(), CALL)
    assert len(asked) == 1
