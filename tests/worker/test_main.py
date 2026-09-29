"""Tests for the worker process: registering under its fleet, and the overflow's gate."""

import pytest
from livekit.agents import AgentServer

from pinecall.domain.errors import SettingsRefused
from pinecall.process.settings import Settings
from pinecall.worker.main import CLOSED, INITIALIZE_S, OPEN, OverflowGate, overflow_of, server_of

REGISTRABLE = {
    "LIVEKIT_URL": "ws://127.0.0.1:7880",
    "LIVEKIT_API_KEY": "APIfake",
    "LIVEKIT_API_SECRET": "fake-secret",
    "PINECALL_FLEET": "pinecall-sandbox",
}


def settings_with(**told: str) -> Settings:
    """Settings of a worker that could register, with what the test changes."""
    return Settings.model_validate({**REGISTRABLE, **told})


def test_a_worker_registers_under_its_fleets_name_only(monkeypatch: pytest.MonkeyPatch) -> None:
    named: list[str] = []
    original = AgentServer.rtc_session

    def recorded(
        server: AgentServer, *args: object, agent_name: str = "", **told: object
    ) -> object:
        named.append(agent_name)
        return original(server, *args, agent_name=agent_name, **told)

    monkeypatch.setattr(AgentServer, "rtc_session", recorded)
    server_of(settings_with())
    assert named == ["pinecall-sandbox"]


def test_both_servers_give_a_new_process_the_time_the_plugins_take(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    given: list[object] = []
    original = AgentServer.__init__

    def recorded(server: AgentServer, *args: object, **told: object) -> None:
        given.append(told.get("initialize_process_timeout"))
        original(server, *args, **told)

    monkeypatch.setattr(AgentServer, "__init__", recorded)
    server_of(settings_with())
    overflow_of(settings_with(), OverflowGate())
    assert given == [INITIALIZE_S, INITIALIZE_S]


def test_a_worker_without_its_livekit_pair_is_refused() -> None:
    with pytest.raises(SettingsRefused, match="LIVEKIT_API_SECRET"):
        server_of(settings_with(LIVEKIT_API_SECRET=""))


def test_the_overflow_is_closed_until_the_fleet_is_full() -> None:
    gate = OverflowGate()
    server = overflow_of(settings_with(), gate)
    assert isinstance(server, AgentServer)
    assert gate(server) == CLOSED
    gate.fleet_is_full = True
    assert gate(server) == OPEN
