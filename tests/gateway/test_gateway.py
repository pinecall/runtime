"""Tests for the gateway's state: what a call runs through is the process's own."""

from pinecall.gateway._gateway import Gateway
from tests.conftest import postgres


@postgres
async def test_a_call_runs_through_the_gateways_own_connections_logs_and_calls(
    wired: Gateway,
) -> None:
    serving = wired.serving
    assert (serving.connections, serving.logs, serving.live) == (
        wired.connections,
        wired.logs,
        wired.live,
    )
