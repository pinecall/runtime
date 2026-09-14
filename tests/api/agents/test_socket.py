"""WS /v1/apps as an app sees it: the door, the register, the refusals and the agent's own log."""

import asyncio
from typing import Any

import pytest
from starlette.testclient import TestClient, WebSocketTestSession
from starlette.websockets import WebSocketDisconnect

from pinecall.api.agents.socket import POLICY_VIOLATION
from pinecall.log.entry import Entry
from pinecall.log.store import MemoryStore
from tests.api.conftest import A_KEY
from tests.api.talking import a_door, a_frame, a_register

pytestmark = pytest.mark.unit

APPS = "/v1/apps"


def open_socket(gateway: TestClient, key: str = A_KEY) -> WebSocketTestSession:
    """The upgrade an app makes: the API key as the Authorization header, and nothing else."""
    return gateway.websocket_connect(APPS, headers={"Authorization": f"Bearer {key}"})


# ── the door ────────────────────────────────────────────────────────────────────


def test_a_key_nobody_issued_is_closed_with_a_policy_violation(gateway: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect) as refused:
        with open_socket(gateway, key="pk_live_not_a_key"):
            pass
    assert refused.value.code == POLICY_VIOLATION


def test_a_socket_with_no_authorization_header_never_opens(gateway: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect) as refused:
        with gateway.websocket_connect(APPS):
            pass
    assert refused.value.code == POLICY_VIOLATION


# ── register ────────────────────────────────────────────────────────────────────


def test_a_fake_app_registers_and_is_told_so(gateway: TestClient) -> None:
    with open_socket(gateway) as socket:
        socket.send_json(a_register("clinica-norte", a_door("phone", "+34910000000")))
        entry = socket.receive_json()
    assert entry["type"] == "agent.registered"
    assert entry["agent"] == "clinica-norte"
    assert entry["call"] is None
    assert entry["seq"] == 1
    assert entry["data"]["routes"] == [{"channel": "phone", "number": "+34910000000"}]


def test_the_register_is_written_to_the_agents_own_log(
    gateway: TestClient, store: MemoryStore
) -> None:
    """The live table is memory; the agent's own log is the record that outlives the socket."""
    with open_socket(gateway) as socket:
        socket.send_json(a_register("clinica-norte", a_door("web")))
        socket.receive_json()
    # Arriving and leaving are both written: the socket closing is the agent.detached.
    written = _read(store, "clinica-norte")
    assert [entry.type for entry in written] == ["agent.registered", "agent.detached"]
    assert written[0].call is None
    assert written[1].data["left"] is True


def test_a_second_socket_takes_the_same_agent_and_the_first_keeps_answering(
    gateway: TestClient,
) -> None:
    """Many sockets hold one agent — the rule this replaced is docs/decisions/dispatch.md."""
    with open_socket(gateway) as first:
        first.send_json(a_register("clinica-norte", a_door("web")))
        assert first.receive_json()["type"] == "agent.registered"
        with open_socket(gateway) as second:
            second.send_json(a_register("clinica-norte", a_door("web"), sdk="pinecall/2.0.0"))
            taken = second.receive_json()
        assert taken["type"] == "agent.registered"
        # The first socket is untouched: it still answers, on the agent it still holds.
        first.send_json(a_frame("ping", "clinica-norte"))
        assert first.receive_json()["type"] == "pong"


def test_every_socket_is_handed_an_id_of_its_own(gateway: TestClient) -> None:
    """It is what `WS /v1/chat?app=` names, so two live sockets are never told the same one."""
    with open_socket(gateway) as first, open_socket(gateway) as second:
        first.send_json(a_register("clinica-norte", a_door("web")))
        second.send_json(a_register("clinica-norte", a_door("web")))
        apps = [first.receive_json()["data"]["app"], second.receive_json()["data"]["app"]]
    assert all(app.startswith("app_") for app in apps)
    assert apps[0] != apps[1]


def test_a_reconnect_after_a_disconnect_takes_the_slug_back(gateway: TestClient) -> None:
    with open_socket(gateway) as first:
        first.send_json(a_register("clinica-norte", a_door("phone", "+34910000000")))
        assert first.receive_json()["type"] == "agent.registered"
    with open_socket(gateway) as again:
        again.send_json(a_register("clinica-norte", a_door("phone", "+34910000000")))
        assert again.receive_json()["type"] == "agent.registered"


def test_two_agents_at_the_same_door_are_refused(gateway: TestClient) -> None:
    with open_socket(gateway) as socket:
        socket.send_json(a_register("clinica-norte", a_door("phone", "+34910000000")))
        assert socket.receive_json()["type"] == "agent.registered"
        socket.send_json(a_register("clinica-sur", a_door("phone", "+34910000000")))
        refusal = socket.receive_json()
    assert refusal["data"]["code"] == "refused"
    assert "already answers for agent clinica-norte" in refusal["data"]["message"]


def test_two_agents_with_a_web_door_register_on_one_gateway(gateway: TestClient) -> None:
    """The fleet's whole point: one gateway, one org, two agents, each with its own widget."""
    with open_socket(gateway) as first, open_socket(gateway) as second:
        first.send_json(a_register("clinica-norte", a_door("web")))
        second.send_json(a_register("tienda-sur", a_door("web")))
        held = [first.receive_json(), second.receive_json()]
    assert [entry["type"] for entry in held] == ["agent.registered", "agent.registered"]
    assert [entry["agent"] for entry in held] == ["clinica-norte", "tienda-sur"]


def test_a_web_route_that_brings_a_number_is_refused_by_the_contract(gateway: TestClient) -> None:
    with open_socket(gateway) as socket:
        socket.send_json(a_register("clinica-norte", a_door("web", "+34910000000")))
        refusal = socket.receive_json()
    assert refusal["data"]["code"] == "refused"
    assert "answers at no number" in refusal["data"]["message"]


# ── configure ───────────────────────────────────────────────────────────────────


def test_a_configure_says_which_fields_changed(gateway: TestClient) -> None:
    with open_socket(gateway) as socket:
        socket.send_json(a_register("clinica-norte", a_door("web")))
        socket.receive_json()
        socket.send_json(
            a_frame(
                "agent.configure",
                "clinica-norte",
                {
                    "config": {
                        "greeting": {"say": "Clínica Norte, buenos días."},
                        "language": "es-ES",
                    }
                },
            )
        )
        entry = socket.receive_json()
    assert entry["type"] == "agent.configured"
    assert entry["data"]["changed"] == ["greeting", "language"]


def test_an_irreversible_tool_with_no_read_back_comes_back_as_an_error(gateway: TestClient) -> None:
    """The domain's rule, reached through the wire: the platform never runs one it cannot read."""
    with open_socket(gateway) as socket:
        socket.send_json(a_register("clinica-norte", a_door("web")))
        socket.receive_json()
        socket.send_json(
            a_frame(
                "agent.configure",
                "clinica-norte",
                {"config": {"tools": [_a_tool(side_effect="irreversible")]}},
                id="cmd_7",
            )
        )
        refusal = socket.receive_json()
    assert refusal["type"] == "error"
    assert refusal["data"]["code"] == "refused"
    assert refusal["data"]["id"] == "cmd_7"
    assert refusal["data"]["command"] == "agent.configure"
    assert "confirm template" in refusal["data"]["message"]


def test_a_configure_before_a_register_is_refused(gateway: TestClient) -> None:
    with open_socket(gateway) as socket:
        socket.send_json(a_frame("agent.configure", "clinica-norte", {"config": {}}))
        refusal = socket.receive_json()
    assert refusal["data"]["code"] == "refused"
    assert "not registered on this socket" in refusal["data"]["message"]


# ── the rest of the frames ──────────────────────────────────────────────────────


def test_a_call_scoped_command_with_no_session_says_so(gateway: TestClient) -> None:
    with open_socket(gateway) as socket:
        socket.send_json(a_register("clinica-norte", a_door("web")))
        socket.receive_json()
        socket.send_json(a_frame("agent.say", "clinica-norte", {"text": "hola"}, id="cmd_1"))
        refusal = socket.receive_json()
    assert refusal["data"]["code"] == "no_session"
    assert refusal["data"]["id"] == "cmd_1"


def test_a_command_the_protocol_never_heard_of_is_named_in_the_refusal(gateway: TestClient) -> None:
    with open_socket(gateway) as socket:
        socket.send_json(a_frame("agent.levitate", "clinica-norte"))
        refusal = socket.receive_json()
    assert refusal["data"]["code"] == "unknown_command"
    assert "agent.levitate" in refusal["data"]["message"]


def test_a_frame_that_is_not_a_command_is_refused_without_reaching_a_log(
    gateway: TestClient, store: MemoryStore
) -> None:
    with open_socket(gateway) as socket:
        socket.send_json({"nothing": "of the sort"})
        refusal = socket.receive_json()
    assert refusal["data"]["code"] == "bad_shape"
    assert refusal["seq"] == 0
    assert _read(store, "") == []


def test_ping_is_answered_with_pong(gateway: TestClient) -> None:
    with open_socket(gateway) as socket:
        socket.send_json(a_frame("ping", "clinica-norte"))
        pong = socket.receive_json()
    assert pong["type"] == "pong"
    assert pong["ephemeral"] is True
    assert pong["data"]["ts"] > 0


def _a_tool(**changes: Any) -> dict[str, Any]:
    """A book_slot the model could call, with whatever this test wants to break about it."""
    return {
        "name": "book_slot",
        "description": "Book the slot the caller chose.",
        "parameters": {"type": "object", "properties": {"at": {"type": "string"}}},
        **changes,
    }


def _read(store: MemoryStore, agent: str) -> list[Entry]:
    """The agent's own log, read the way anybody else reads it."""
    return asyncio.run(store.agent_since(agent))
