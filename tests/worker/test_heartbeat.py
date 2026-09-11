"""The heartbeat beside livekit's socket: what it sends, what a cordon does, how it leaves."""

from __future__ import annotations

import signal
from typing import Any

import pytest

from pinecall.fleet import Heartbeat
from pinecall.worker.heartbeat import CORDONED_EXIT, Heartbeats, load_of
from tests.worker.fakes import Seen, a_gateway

pytestmark = pytest.mark.unit

HEARTBEAT = "/v1/fleet/heartbeat"


class FakeServer:
    """The four things of an AgentServer a heartbeat reads: jobs, draining, the gate, drain()."""

    def __init__(self, active: int = 2, load_fnc: Any = None) -> None:
        self.active_jobs = [object()] * active
        self.draining = False
        self.load_fnc = load_fnc
        self.drained = False

    async def drain(self) -> None:
        self.draining = True
        self.drained = True


def half(_server: object) -> float:
    """A gate of the fleet's shape: it takes the server and answers a load."""
    return 0.5


def quarter(_server: object) -> float:
    return 0.25


def beats(server: FakeServer, seen: list[Seen], cordoned: bool = False) -> Heartbeats:
    gateway = a_gateway({HEARTBEAT: {"cordoned": cordoned, "full": False}}, seen)
    return Heartbeats(server, gateway, "pinecall-worker-1", 4)  # pyright: ignore[reportArgumentType]


async def test_one_beat_carries_the_name_the_jobs_held_the_measured_max_and_the_load() -> None:
    seen: list[Seen] = []
    pulse = beats(FakeServer(active=2, load_fnc=half), seen)
    standing = await pulse._gateway.heartbeat(pulse._a_beat())  # pyright: ignore[reportPrivateUsage]
    assert seen[0].body == {
        "worker": "pinecall-worker-1",
        "active": 2,
        "max_jobs": 4,
        "load": 0.5,
        "draining": False,
    }
    assert standing.cordoned is False
    assert pulse.exit_code() == 0


def test_the_load_is_read_off_the_gate_livekit_was_handed_in_either_arity() -> None:
    """livekit accepts a gate that takes the server and one that takes nothing (worker.py:1303)."""
    assert load_of(FakeServer(load_fnc=quarter)) == 0.25  # pyright: ignore[reportArgumentType]
    assert load_of(FakeServer(load_fnc=lambda: 0.0)) == 0.0  # pyright: ignore[reportArgumentType]
    assert load_of(FakeServer(load_fnc=None)) == 0.0  # pyright: ignore[reportArgumentType]


async def test_a_cordon_drains_the_server_then_leaves_with_the_exit_that_keeps_it_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raised: list[int] = []
    monkeypatch.setattr(signal, "raise_signal", raised.append)
    server = FakeServer(active=1)
    pulse = beats(server, [], cordoned=True)
    await pulse.run()
    assert server.drained is True
    assert raised == [signal.SIGTERM]
    assert pulse.exit_code() == CORDONED_EXIT


def test_the_beat_is_the_shape_the_hub_validates() -> None:
    beat = Heartbeat(worker="w", active=0, max_jobs=None, load=0.1, draining=False)
    assert beat.max_jobs is None
