"""Many app sockets on one agent: which one takes a call, which one hears it, which is refused."""

from __future__ import annotations

from typing import Any

import pytest
from starlette.testclient import TestClient, WebSocketTestSession
from starlette.websockets import WebSocketDisconnect

from pinecall.api.live import Live
from pinecall.auth.bearer import POLICY_VIOLATION
from pinecall.auth.keys import KeyRecord
from pinecall.auth.keys_memory import MemoryKeys
from pinecall.worker.gateway_client import CONTEXT
from tests.api.conftest import A_KEY, A_RECORD, AGENT, APPS, CHAT
from tests.api.talking import a_caller, a_door, a_frame, a_register, an_app, entry_until
from tests.api.test_worker_doors import CALL, a_context

pytestmark = pytest.mark.unit

# The clinic next door: its own key, its own org, and never a way into this one.
ANOTHER_KEY = "pk_test_the_clinic_next_door"
ANOTHER_RECORD = KeyRecord(key_id="k_2", org="vecina", label="the org next door")
ANOTHER_AGENT = "clinica-vecina"
A_NUMBER = "+34910000000"


@pytest.fixture
def keys() -> MemoryKeys:
    """Two orgs knock at this gateway, so a socket of the wrong one has an id to be refused by."""
    return MemoryKeys({A_KEY: A_RECORD, ANOTHER_KEY: ANOTHER_RECORD})


# ── which socket takes the call ─────────────────────────────────────────────────


def test_a_call_takes_the_newest_socket_and_the_older_one_hears_nothing_of_it(
    gateway: TestClient, live: Live
) -> None:
    """Criterion 3: the socket is chosen when the call opens, and every entry goes down that one."""
    with an_app(gateway) as older, an_app(gateway) as newest:
        holding(older)
        newest_app = holding(newest)
        with a_caller(gateway) as caller:
            started = caller.receive_json()
            assert started["type"] == "call.started"
            assert heard(newest) == "call.started"
            # The binding is a fact this process keeps for as long as the call is open.
            assert live.app_of(started["call"]) == newest_app
        assert heard_nothing(older), "the older socket was fed a call it never took"


def test_a_caller_that_names_the_older_app_is_served_by_that_one(
    gateway: TestClient, live: Live
) -> None:
    """Criterion 4, and what makes `pinecall chat` the Rails console: it is served by itself."""
    with an_app(gateway) as older, an_app(gateway) as newest:
        older_app = holding(older)
        holding(newest)
        with a_caller_asking_for(gateway, older_app) as caller:
            started = caller.receive_json()
            assert started["type"] == "call.started"
            assert heard(older) == "call.started"
            assert live.app_of(started["call"]) == older_app
        assert heard_nothing(newest), "the newest socket was fed a call somebody else took"


def test_a_caller_naming_an_app_that_is_not_holding_the_agent_is_refused_with_a_reason(
    gateway: TestClient,
) -> None:
    """A socket of another org cannot be holding this agent, so it is that one same refusal."""
    with an_app(gateway) as mine, the_clinic_next_door(gateway) as neighbour:
        holding(mine)
        neighbour.send_json(a_register(ANOTHER_AGENT, a_door("phone", A_NUMBER)))
        stranger = str(neighbour.receive_json()["data"]["app"])
        with (
            a_caller_asking_for(gateway, stranger) as caller,
            pytest.raises(WebSocketDisconnect) as refused,
        ):
            caller.receive_json()
    assert refused.value.code == POLICY_VIOLATION
    assert stranger in refused.value.reason
    assert AGENT in refused.value.reason


def test_a_call_the_worker_opened_claims_a_socket_the_very_same_way(
    gateway: TestClient, live: Live
) -> None:
    """One question, one answer: the door a call arrives through never changes who serves it."""
    with an_app(gateway) as older, an_app(gateway) as newest:
        older_app = holding(older)
        holding(newest)
        assert posted(gateway, "/v1/calls", an_opening(older_app))[0] == 200
        assert heard(older) == "call.ringing"
        assert live.app_of(CALL) == older_app
        assert heard_nothing(newest), "the newest socket was fed a call the worker gave away"


def test_a_worker_naming_an_app_that_is_not_holding_the_agent_is_refused_with_a_reason(
    gateway: TestClient,
) -> None:
    """Refused, never quietly ignored: a claim that fell through would take a real phone call."""
    with an_app(gateway) as mine, the_clinic_next_door(gateway) as neighbour:
        holding(mine)
        neighbour.send_json(a_register(ANOTHER_AGENT, a_door("phone", A_NUMBER)))
        stranger = str(neighbour.receive_json()["data"]["app"])
        status, refused = posted(gateway, "/v1/calls", an_opening(stranger))
    assert status == 409
    assert stranger in refused["detail"]
    assert AGENT in refused["detail"]


# ── a console takes only the call it opened itself ──────────────────────────────


def test_a_call_nobody_claimed_skips_the_console_and_the_console_hears_nothing_of_it(
    gateway: TestClient, live: Live
) -> None:
    """The bug this card closes: a real caller answered from whoever left a `pinecall chat` open."""
    with an_app(gateway) as server, an_app(gateway) as console:
        server_app = holding(server)
        holding(console, takes_unclaimed=False)
        with a_caller(gateway) as caller:
            started = caller.receive_json()
            assert started["type"] == "call.started"
            assert heard(server) == "call.started"
            assert live.app_of(started["call"]) == server_app
        assert heard_nothing(console), "a console was handed a call it never opened"


def test_the_consoles_own_call_still_runs_on_the_console(gateway: TestClient, live: Live) -> None:
    """Taking no unclaimed call costs a console nothing: `?app=` still names it and still wins."""
    with an_app(gateway) as server, an_app(gateway) as console:
        holding(server)
        console_app = holding(console, takes_unclaimed=False)
        with a_caller_asking_for(gateway, console_app) as caller:
            started = caller.receive_json()
            assert started["type"] == "call.started"
            assert heard(console) == "call.started"
            assert live.app_of(started["call"]) == console_app
        assert heard_nothing(server), "the server was fed the console's own call"


def test_a_caller_of_an_agent_only_consoles_hold_is_refused_with_what_to_start(
    gateway: TestClient,
) -> None:
    """Refused, not served in silence: nobody is answering this agent in public right now."""
    with an_app(gateway) as console:
        holding(console, takes_unclaimed=False)
        with a_caller(gateway) as caller, pytest.raises(WebSocketDisconnect) as refused:
            caller.receive_json()
        assert heard_nothing(console), "a refused call was still put on the console's socket"
    assert refused.value.code == POLICY_VIOLATION
    assert AGENT in refused.value.reason
    assert "pinecall start" in refused.value.reason


def test_a_worker_opening_a_call_on_an_agent_only_consoles_hold_is_refused(
    gateway: TestClient,
) -> None:
    """The phone call itself: the worker is told no before the caller has been greeted."""
    with an_app(gateway) as console:
        holding(console, takes_unclaimed=False)
        status, refused = posted(gateway, "/v1/calls", an_opening())
        assert heard_nothing(console), "a refused call was still put on the console's socket"
    assert status == 409
    assert AGENT in refused["detail"]
    assert "pinecall start" in refused["detail"]


# ── when the socket serving a call goes away ────────────────────────────────────


def test_the_socket_left_standing_takes_over_the_call_and_hears_call_attached(
    gateway: TestClient, live: Live
) -> None:
    """A call is its agent's: the process that answered it went, and the one left has it."""
    with an_app(gateway) as older:
        older_app = holding(older)
        with an_app(gateway) as newest:
            holding(newest)
            assert posted(gateway, "/v1/calls", an_opening())[0] == 200
            assert heard(newest) == "call.ringing"
        assert heard(older) == "call.attached"
        assert posted(gateway, f"/v1/calls/{CALL}/events", a_started())[0] == 204
        assert heard(older) == "call.started"
        assert live.app_of(CALL) == older_app


def test_a_console_left_standing_takes_no_call_whose_app_went_away(
    gateway: TestClient, live: Live
) -> None:
    """A console never takes a call nobody named, and a call its process left is nobody's."""
    with an_app(gateway) as console:
        holding(console, takes_unclaimed=False)
        with an_app(gateway) as server:
            holding(server)
            assert posted(gateway, "/v1/calls", an_opening())[0] == 200
            assert heard(server) == "call.ringing"
        assert posted(gateway, f"/v1/calls/{CALL}/events", a_started())[0] == 204
        assert live.app_of(CALL) is None
        assert heard_nothing(console), "a console adopted a live call"


def test_the_next_process_to_register_the_agent_takes_the_calls_the_last_one_left(
    gateway: TestClient, live: Live
) -> None:
    """A deploy: the only process goes, the call waits parked, and the next one to arrive has it."""
    with an_app(gateway) as leaving:
        holding(leaving)
        assert posted(gateway, "/v1/calls", an_opening())[0] == 200
        assert heard(leaving) == "call.ringing"
    assert live.app_of(CALL) is None
    with an_app(gateway) as arriving:
        arriving_app = holding_and_hearing(arriving)
        assert live.app_of(CALL) == arriving_app


def test_a_process_that_drains_hands_its_call_to_the_other_and_says_how_many(
    gateway: TestClient, live: Live
) -> None:
    """agent.drain: the call moves before the process goes, and it is handed no new one."""
    with an_app(gateway) as older:
        older_app = holding(older)
        with an_app(gateway) as leaving:
            holding(leaving)
            assert posted(gateway, "/v1/calls", an_opening())[0] == 200
            assert heard(leaving) == "call.ringing"
            leaving.send_json(a_frame("agent.drain", AGENT, {}))
            drained: dict[str, Any] = leaving.receive_json()
            assert drained["type"] == "agent.draining"
            assert (drained["data"]["handed"], drained["data"]["parked"]) == (1, 0)
            assert heard(older) == "call.attached"
            assert live.app_of(CALL) == older_app


def test_a_process_that_drains_alone_parks_its_call_for_the_next_one(
    gateway: TestClient, live: Live
) -> None:
    """Nobody else holds the agent: the call waits for the process that is starting."""
    with an_app(gateway) as leaving:
        holding(leaving)
        assert posted(gateway, "/v1/calls", an_opening())[0] == 200
        assert heard(leaving) == "call.ringing"
        leaving.send_json(a_frame("agent.drain", AGENT, {}))
        drained: dict[str, Any] = leaving.receive_json()
        assert (drained["data"]["handed"], drained["data"]["parked"]) == (0, 1)
        assert live.app_of(CALL) is None
        refused, _ = posted(gateway, "/v1/calls", an_opening())
        assert refused == 409, "a draining process was handed a new call"


# ── what a test says to the doors ───────────────────────────────────────────────


def holding(app_socket: WebSocketTestSession, takes_unclaimed: bool | None = None) -> str:
    """One more socket holding the clinic, and the id agent.registered hands back to it."""
    app_socket.send_json(a_register(AGENT, a_door("web"), takes_unclaimed=takes_unclaimed))
    registered: dict[str, Any] = app_socket.receive_json()
    config: dict[str, object] = {"language": "es"}
    app_socket.send_json(a_frame("agent.configure", AGENT, {"config": config}))
    app_socket.receive_json()
    return str(registered["data"]["app"])


def the_clinic_next_door(gateway: TestClient) -> WebSocketTestSession:
    """The neighbouring org's own process, on the same gateway, with its own key."""
    return gateway.websocket_connect(APPS, headers={"Authorization": f"Bearer {ANOTHER_KEY}"})


def a_caller_asking_for(gateway: TestClient, app: str) -> WebSocketTestSession:
    """The caller's browser naming the one app socket it wants to be served by."""
    return gateway.websocket_connect(
        f"{CHAT}?agent={AGENT}&app={app}", headers={"Authorization": f"Bearer {A_KEY}"}
    )


def heard(app_socket: WebSocketTestSession) -> str:
    """The type of the next entry this socket is handed."""
    frame: dict[str, Any] = app_socket.receive_json()
    return str(frame["type"])


# A socket's frames arrive in order, so an idle socket answers a ping with a pong and a socket that
# was wrongly fed somebody else's call hands back that call's entry instead.
def heard_nothing(app_socket: WebSocketTestSession) -> bool:
    """True when this socket has no entry queued for it: its next frame is its own pong."""
    app_socket.send_json(a_frame("ping", AGENT))
    return heard(app_socket) == "pong"


def an_opening(app: str | None = None) -> dict[str, Any]:
    """The body POST /v1/calls takes: the agent, all the worker knows, and the socket it claims."""
    said: dict[str, Any] = {
        "agent": AGENT,
        "context": CONTEXT.dump_python(a_context(), mode="json"),
    }
    return said if app is None else said | {"app": app}


def a_started() -> dict[str, Any]:
    """One entry the worker hands over once its call is open."""
    return {
        "type": "call.started",
        "data": {
            "channel": "web",
            "direction": "inbound",
            "from": "visitor_1",
            "to": AGENT,
            "caller": None,
            "started_at": 1_757_000_000.0,
        },
    }


def posted(client: TestClient, path: str, said: Any) -> tuple[int, dict[str, Any]]:
    """One POST at a worker's door, as the status it answered with and whatever body it wrote."""
    handle: Any = client
    answer: Any = handle.post(path, json=said, headers={"Authorization": f"Bearer {A_KEY}"})
    status: int = answer.status_code
    body: dict[str, Any] = {} if status == 204 else answer.json()
    return status, body


def test_a_dial_over_the_app_socket_is_sent_to_the_door_that_places_calls(
    gateway: TestClient,
) -> None:
    """call.dial is agent-scoped, so it lands on this socket; placing a call is not this socket's.
    Before the door existed it was refused as `no_session`, which named the wrong problem."""
    with an_app(gateway) as app_socket:
        app_socket.send_json(a_register(AGENT))
        app_socket.receive_json()
        app_socket.send_json(a_frame("call.dial", AGENT, {"to": "+34600123456"}))
        refused = entry_until(app_socket, "error")
        assert refused["data"]["code"] == "no_handler"
        assert "POST /v1/agents/{slug}/dial" in refused["data"]["message"]


def holding_and_hearing(app_socket: WebSocketTestSession) -> str:
    """A socket registering the clinic while a call of it waits: call.attached comes first."""
    app_socket.send_json(a_register(AGENT, a_door("web")))
    registered: dict[str, Any] = app_socket.receive_json()
    assert heard(app_socket) == "call.attached"
    return str(registered["data"]["app"])
