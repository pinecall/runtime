"""Two worlds on one gateway: a production key and a development key of ONE org never cross."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from starlette.testclient import TestClient, WebSocketTestSession

from pinecall.auth.keys import KeyRecord, MemoryKeys
from pinecall.types import DEVELOPMENT, PRODUCTION, Route
from pinecall.worker.client import CONTEXT
from tests.api.conftest import A_KEY, A_RECORD, AGENT, APPS, CHAT, over_the_asgi_app
from tests.api.talking import a_context, a_door, a_register, entry_until, got, hung_up_by_the_app

pytestmark = pytest.mark.unit

# The same org, issued a second key for the laptop where the agent is being written.
A_DEV_KEY = "pk_test_bernas_laptop"
A_DEV_RECORD = KeyRecord(key_id="k_dev", org=A_RECORD.org, label="berna's laptop", env=DEVELOPMENT)
A_NUMBER = "+34910000000"


@pytest.fixture
def keys() -> MemoryKeys:
    """One org, two keys: the box's, in production, and the laptop's, in development."""
    return MemoryKeys({A_KEY: A_RECORD, A_DEV_KEY: A_DEV_RECORD})


def an_app_on(gateway: TestClient, key: str) -> WebSocketTestSession:
    """A process on the app socket, with whichever of the two keys the test names."""
    return gateway.websocket_connect(APPS, headers={"Authorization": f"Bearer {key}"})


def holding(
    app_socket: WebSocketTestSession, door: dict[str, object] | None = None
) -> dict[str, Any]:
    """One socket registering the clinic at one door, and what the gateway answered."""
    app_socket.send_json(a_register(AGENT, door or a_door("web")))
    answer: dict[str, Any] = app_socket.receive_json()
    return answer


def agents_seen_by(gateway: TestClient, key: str) -> list[str]:
    status, body = got(gateway, "/v1/agents", key)
    assert status == 200
    return [str(held["slug"]) for held in body["agents"]]


def test_each_key_sees_the_agent_held_in_its_own_world_and_the_register_says_which(
    gateway: TestClient,
) -> None:
    """The same slug, held by the box and by the laptop at once: two agents to the gateway."""
    with an_app_on(gateway, A_KEY) as deployed, an_app_on(gateway, A_DEV_KEY) as written:
        assert holding(deployed)["data"]["env"] == PRODUCTION
        assert agents_seen_by(gateway, A_DEV_KEY) == [], "nothing is held in development yet"
        assert holding(written)["data"]["env"] == DEVELOPMENT
        assert agents_seen_by(gateway, A_KEY) == [AGENT]
        assert agents_seen_by(gateway, A_DEV_KEY) == [AGENT]
    assert agents_seen_by(gateway, A_KEY) == []


def test_the_development_key_is_refused_the_number_the_box_answers(gateway: TestClient) -> None:
    """A number rings in one place, and the refusal names the world that holds it."""
    with an_app_on(gateway, A_KEY) as deployed, an_app_on(gateway, A_DEV_KEY) as written:
        assert holding(deployed, a_door("phone", A_NUMBER))["type"] == "agent.registered"
        refused = holding(written, a_door("phone", A_NUMBER))
    assert refused["type"] == "error"
    assert f"already answers for agent {AGENT} in production" in refused["data"]["message"]


def test_whoami_says_which_world_the_key_opens(gateway: TestClient) -> None:
    _, deployed = got(gateway, "/v1/whoami", A_KEY)
    _, written = got(gateway, "/v1/whoami", A_DEV_KEY)
    assert (deployed["env"], written["env"]) == (PRODUCTION, DEVELOPMENT)
    assert written["label"] == "berna's laptop"


async def test_the_worker_reads_the_doors_of_its_own_world_and_no_other(
    gateway: TestClient,
) -> None:
    """GET /v1/routes on the laptop's key answers the laptop's doors, and never the box's number."""
    with an_app_on(gateway, A_KEY) as deployed, an_app_on(gateway, A_DEV_KEY) as written:
        holding(deployed, a_door("phone", A_NUMBER))
        holding(written)
        async with over_the_asgi_app(f"Bearer {A_DEV_KEY}") as laptop:
            answered = (await laptop.get("/v1/routes")).json()
        async with over_the_asgi_app(f"Bearer {A_KEY}") as box:
            deployed_doors = (await box.get("/v1/routes")).json()
    assert [(route["channel"], route["env"]) for route in answered] == [("web", DEVELOPMENT)]
    assert [(route["channel"], route["env"]) for route in deployed_doors] == [("phone", PRODUCTION)]


async def test_a_worker_on_one_key_cannot_open_a_call_on_the_other_worlds_route(
    gateway: TestClient,
) -> None:
    """The route the worker resolved says which world; the key says which it may open. 403."""
    with an_app_on(gateway, A_KEY) as deployed:
        holding(deployed)
        context = a_context("call_crossing")
        assert context.route.env == PRODUCTION
        async with over_the_asgi_app(f"Bearer {A_DEV_KEY}") as laptop:
            refused = await laptop.post(
                "/v1/calls",
                json={"agent": AGENT, "context": CONTEXT.dump_python(context, mode="json")},
            )
    assert refused.status_code == httpx.codes.FORBIDDEN
    assert refused.json()["detail"] == (
        "this key opens development, and that call's route answers in production"
    )


def test_a_chat_on_the_development_key_is_served_by_the_laptop_and_says_so_on_call_started(
    gateway: TestClient,
) -> None:
    """The box's socket hears nothing of it, and the log files the call under development."""
    with an_app_on(gateway, A_KEY) as deployed, an_app_on(gateway, A_DEV_KEY) as written:
        holding(deployed)
        holding(written)
        with gateway.websocket_connect(
            f"{CHAT}?agent={AGENT}", headers={"Authorization": f"Bearer {A_DEV_KEY}"}
        ):
            started = entry_until(written, "call.started")
            hung_up_by_the_app(written, started["call"])
        assert started["data"]["env"] == DEVELOPMENT
        deployed.send_json({"type": "ping", "agent": AGENT, "call": None, "data": {}})
        assert deployed.receive_json()["type"] == "pong", "the box's socket heard the laptop's call"


def test_a_route_is_one_worlds_and_a_call_context_reads_it_off_the_door() -> None:
    route = Route(org="clinica", agent=AGENT, channel="web", env=DEVELOPMENT)
    assert a_context("call_1").env == PRODUCTION, "a route that says nothing is production's"
    assert route.env == DEVELOPMENT
