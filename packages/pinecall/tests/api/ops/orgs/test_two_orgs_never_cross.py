"""Two organisations on one gateway: each sees its agents, routes, calls and usage, and no more."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from starlette.testclient import TestClient, WebSocketTestSession
from starlette.websockets import WebSocketDisconnect

from pinecall.auth.bearer import POLICY_VIOLATION
from pinecall.auth.keys import KeyRecord
from pinecall.auth.keys_memory import MemoryKeys
from pinecall.log.store import MemoryStore
from pinecall.orgs.records_memory import MemoryOrgs
from pinecall.types import Org
from pinecall_testkit.usage import A_SUMMARY
from tests.api.conftest import A_KEY, A_RECORD, AGENT, AN_ORG, APPS
from tests.api.talking import a_caller, a_door, a_register, an_app, got, hung_up_by_the_app

pytestmark = pytest.mark.unit

# The shop across the street: its own key, its own org, and never a way into the clinic's.
ANOTHER_KEY = "pk_test_the_shop_across_the_street"
ANOTHER_RECORD = KeyRecord(key_id="k_2", org="tienda", label="the shop")
ANOTHER_ORG = Org(id="tienda", slug="tienda", name="Tienda Sur")
ANOTHER_AGENT = "tienda-sur"


@pytest.fixture
def keys() -> MemoryKeys:
    """Two orgs knock at this gateway."""
    return MemoryKeys({A_KEY: A_RECORD, ANOTHER_KEY: ANOTHER_RECORD})


@pytest.fixture
def orgs() -> MemoryOrgs:
    """Both are tenants the operator created."""
    return MemoryOrgs([AN_ORG, ANOTHER_ORG])


def another_app(gateway: TestClient) -> WebSocketTestSession:
    """The shop's process on the app socket, with the shop's key."""
    return gateway.websocket_connect(APPS, headers={"Authorization": f"Bearer {ANOTHER_KEY}"})


# The shop answers at a number: a door is (channel, number), the widget has none, and so one
# gateway has one web agent — which is the clinic's here.
A_NUMBER = "+34910000000"


def holding(
    app_socket: WebSocketTestSession, agent: str, door: dict[str, object] | None = None
) -> dict[str, Any]:
    """One socket registering one agent at one door, and what the gateway answered."""
    app_socket.send_json(a_register(agent, door or a_door("web")))
    answer: dict[str, Any] = app_socket.receive_json()
    return answer


def agents_seen_by(gateway: TestClient, key: str) -> list[str]:
    status, body = got(gateway, "/v1/agents", key)
    assert status == 200
    return [str(held["slug"]) for held in body["agents"]]


def routes_seen_by(gateway: TestClient, key: str) -> list[str]:
    handle: Any = gateway
    answer: Any = handle.get("/v1/routes", headers={"Authorization": f"Bearer {key}"})
    assert answer.status_code == 200
    return [str(route["agent"]) for route in answer.json()]


def test_each_org_lists_its_own_agents_and_routes_and_nobody_elses(gateway: TestClient) -> None:
    """Criterion 1, the live tables: the key is the org, and the org sees what it holds."""
    with an_app(gateway) as ours, another_app(gateway) as theirs:
        assert holding(ours, AGENT)["type"] == "agent.registered"
        theirs_door = a_door("phone", A_NUMBER)
        assert holding(theirs, ANOTHER_AGENT, theirs_door)["type"] == "agent.registered"
        assert agents_seen_by(gateway, A_KEY) == [AGENT]
        assert agents_seen_by(gateway, ANOTHER_KEY) == [ANOTHER_AGENT]
        # And the doors: neither org has a row, because a door is a row somebody typed and
        # holding an agent types none. What each key sees of the other's table is nothing either.
        assert routes_seen_by(gateway, A_KEY) == []
        assert routes_seen_by(gateway, ANOTHER_KEY) == []


def test_a_slug_one_org_registered_is_refused_to_the_other(gateway: TestClient) -> None:
    """A slug is one org's for as long as its log exists, whether or not a socket holds it."""
    with an_app(gateway) as ours, another_app(gateway) as theirs:
        holding(ours, AGENT)
        refused = holding(theirs, AGENT, a_door("phone", A_NUMBER))
        assert refused["type"] == "error"
        assert "one org's" in refused["data"]["message"]
    # The clinic's socket is gone; the word is still the clinic's.
    with another_app(gateway) as theirs:
        assert holding(theirs, AGENT, a_door("phone", A_NUMBER))["type"] == "error"


def test_an_org_never_reads_another_orgs_agent_log_or_calls(gateway: TestClient) -> None:
    """Criterion 1, the log: another org's key is 403 on every door that reads one."""
    with an_app(gateway) as ours:
        holding(ours, AGENT)
        assert got(gateway, f"/v1/agents/{AGENT}/calls", ANOTHER_KEY)[0] == 403
        assert got(gateway, f"/v1/agents/{AGENT}/sessions", ANOTHER_KEY)[0] == 403
        assert got(gateway, f"/v1/agents/{AGENT}/calls", A_KEY)[0] == 200
        with a_caller(gateway) as caller:
            started: dict[str, Any] = caller.receive_json()
            call = str(started["call"])
            assert got(gateway, f"/v1/calls/{call}/events", ANOTHER_KEY)[0] == 403
            assert got(gateway, f"/v1/calls/{call}/state", ANOTHER_KEY)[0] == 403
            assert got(gateway, f"/v1/calls/{call}/events", A_KEY)[0] == 200
            assert got(gateway, f"/v1/agents/{AGENT}/sessions", ANOTHER_KEY)[0] == 403
            hung_up_by_the_app(ours, call)


def test_an_org_never_opens_a_text_call_to_another_orgs_agent(gateway: TestClient) -> None:
    """Criterion 1, the chat socket: the shop's key is closed with a sentence naming the agent."""
    with an_app(gateway) as ours:
        holding(ours, AGENT)
        with (
            pytest.raises(WebSocketDisconnect) as refused,
            gateway.websocket_connect(
                f"/v1/chat?agent={AGENT}", headers={"Authorization": f"Bearer {ANOTHER_KEY}"}
            ) as caller,
        ):
            caller.receive_json()
        assert refused.value.code == POLICY_VIOLATION
        assert AGENT in refused.value.reason and "another org" in refused.value.reason


def test_an_org_never_writes_into_another_orgs_open_call(gateway: TestClient) -> None:
    """Criterion 1, the write side: a call id is not a licence to append to, or seal, that log."""
    handle: Any = gateway
    with an_app(gateway) as ours:
        holding(ours, AGENT)
        with a_caller(gateway) as caller:
            call = str(caller.receive_json()["call"])
            theirs = {"Authorization": f"Bearer {ANOTHER_KEY}"}
            said = {"type": "call.ended", "data": {"reason": "caller_hung_up"}}
            assert (
                handle.post(f"/v1/calls/{call}/events", json=said, headers=theirs).status_code
                == 403
            )
            assert handle.post(f"/v1/calls/{call}/sealed", headers=theirs).status_code == 403
            assert got(gateway, f"/v1/calls/{call}/events", A_KEY)[0] == 200
            hung_up_by_the_app(ours, call)


async def test_usage_is_read_per_org_and_never_summed_across(
    ops_http: httpx.AsyncClient, store: MemoryStore
) -> None:
    """Criterion 1, the facts: the projection files every row under the log's owner."""
    await store.owned("CA_ours", AGENT, A_RECORD.org)
    await store.append("CA_ours", AGENT, "call.summary", A_SUMMARY)
    await store.owned("CA_theirs", ANOTHER_AGENT, ANOTHER_RECORD.org)
    await store.append("CA_theirs", ANOTHER_AGENT, "call.summary", A_SUMMARY)
    theirs = (await ops_http.get("/v1/ops/usage", params={"org": ANOTHER_ORG.slug})).json()
    assert [row["call"] for row in theirs["rows"]] == ["CA_theirs"]
    assert list(theirs["totals"]) == [ANOTHER_ORG.id]
    everybody = (await ops_http.get("/v1/ops/usage")).json()
    assert sorted(everybody["totals"]) == [A_RECORD.org, ANOTHER_ORG.id]
