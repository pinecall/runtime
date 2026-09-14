"""Two worlds on one gateway: a production key and a sandbox key of ONE org never cross."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from starlette.testclient import TestClient, WebSocketTestSession

from pinecall.auth.keys import KeyRecord, MemoryKeys
from pinecall.orgs.table import MemoryOrgs
from pinecall.types import PRODUCTION, ROLE_SCOPES, SANDBOX, Quotas, Route
from pinecall.worker.client import CONTEXT
from tests.api.conftest import A_KEY, A_RECORD, AGENT, APPS, CHAT, over_the_asgi_app
from tests.api.talking import a_context, a_door, a_register, entry_until, got, hung_up_by_the_app
from tests.api.tokens.test_the_door import minted

pytestmark = pytest.mark.unit

# The same org, issued a second key for the laptop where the agent is being written.
A_DEV_KEY = "pk_test_bernas_laptop"
A_DEV_RECORD = KeyRecord(key_id="k_dev", org=A_RECORD.org, label="berna's laptop", env=SANDBOX)
A_NUMBER = "+34910000000"


@pytest.fixture
def keys() -> MemoryKeys:
    """One org, two keys: the box's, in production, and the laptop's, in sandbox."""
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
        assert agents_seen_by(gateway, A_DEV_KEY) == [], "nothing is held in sandbox yet"
        assert holding(written)["data"]["env"] == SANDBOX
        assert agents_seen_by(gateway, A_KEY) == [AGENT]
        assert agents_seen_by(gateway, A_DEV_KEY) == [AGENT]
    assert agents_seen_by(gateway, A_KEY) == []


def test_the_sandbox_key_is_refused_the_number_the_box_answers(gateway: TestClient) -> None:
    """A number rings in one place, and the refusal names the world that holds it."""
    with an_app_on(gateway, A_KEY) as deployed, an_app_on(gateway, A_DEV_KEY) as written:
        assert holding(deployed, a_door("phone", A_NUMBER))["type"] == "agent.registered"
        refused = holding(written, a_door("phone", A_NUMBER))
    assert refused["type"] == "error"
    assert f"already answers for agent {AGENT} in production" in refused["data"]["message"]


def test_whoami_says_which_world_the_key_opens(gateway: TestClient) -> None:
    _, deployed = got(gateway, "/v1/whoami", A_KEY)
    _, written = got(gateway, "/v1/whoami", A_DEV_KEY)
    assert (deployed["env"], written["env"]) == (PRODUCTION, SANDBOX)
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
    assert [(route["channel"], route["env"]) for route in answered] == [("web", SANDBOX)]
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
        "this key opens sandbox, and that call's route answers in production"
    )


def test_a_chat_on_the_sandbox_key_is_served_by_the_laptop_and_says_so_on_call_started(
    gateway: TestClient,
) -> None:
    """The box's socket hears nothing of it, and the log files the call under sandbox."""
    with an_app_on(gateway, A_KEY) as deployed, an_app_on(gateway, A_DEV_KEY) as written:
        holding(deployed)
        holding(written)
        with gateway.websocket_connect(
            f"{CHAT}?agent={AGENT}", headers={"Authorization": f"Bearer {A_DEV_KEY}"}
        ):
            started = entry_until(written, "call.started")
            hung_up_by_the_app(written, started["call"])
        assert started["data"]["env"] == SANDBOX
        deployed.send_json({"type": "ping", "agent": AGENT, "call": None, "data": {}})
        assert deployed.receive_json()["type"] == "pong", "the box's socket heard the laptop's call"


def test_a_route_is_one_worlds_and_a_call_context_reads_it_off_the_door() -> None:
    route = Route(org="clinica", agent=AGENT, channel="web", env=SANDBOX)
    assert a_context("call_1").env == PRODUCTION, "a route that says nothing is production's"
    assert route.env == SANDBOX


# ── two developers, one world ───────────────────────────────────────────────────

# Two people of the same org, each with a sandbox key of their own. A person's key does not
# open `app` in production at all, so this is the only world where either of them holds anything.
# The developer's preset and not every scope: `team` is what makes a key the org's EYES, and one
# of these two holding it by accident would make every assertion below about a corner vacuous.
BERNAS_KEY = "pk_test_bernas_dev_key"
BERNA = KeyRecord(
    key_id="k_berna",
    org=A_RECORD.org,
    label="cli",
    env=SANDBOX,
    scopes=ROLE_SCOPES["developer"],
    subject="m_berna",
    name="Berna",
)
CARLAS_KEY = "pk_test_carlas_dev_key"
CARLA = KeyRecord(
    key_id="k_carla",
    org=A_RECORD.org,
    label="cli",
    env=SANDBOX,
    scopes=ROLE_SCOPES["developer"],
    subject="m_carla",
    name="Carla",
)


class TestATeamInOneWorld:
    """Two laptops running the same agent: before this, the second took it from the first."""

    @pytest.fixture
    def keys(self) -> MemoryKeys:
        return MemoryKeys({A_KEY: A_RECORD, BERNAS_KEY: BERNA, CARLAS_KEY: CARLA})

    def test_both_hold_it_at_once_and_each_is_answered_their_own_socket(
        self, gateway: TestClient
    ) -> None:
        with an_app_on(gateway, BERNAS_KEY) as bernas, an_app_on(gateway, CARLAS_KEY) as carlas:
            first = holding(bernas)["data"]["app"]
            second = holding(carlas)["data"]["app"]
            assert first != second, "neither register took the agent from the other"
            assert agents_seen_by(gateway, BERNAS_KEY) == [AGENT]
            assert agents_seen_by(gateway, CARLAS_KEY) == [AGENT]

    def test_one_of_them_leaving_leaves_the_other_holding_their_own(
        self, gateway: TestClient
    ) -> None:
        with an_app_on(gateway, CARLAS_KEY) as carlas:
            with an_app_on(gateway, BERNAS_KEY) as bernas:
                holding(bernas)
            holding(carlas)
            assert agents_seen_by(gateway, BERNAS_KEY) == [], "nobody holds it in Berna's corner"
            assert agents_seen_by(gateway, CARLAS_KEY) == [AGENT]

    def test_a_developer_is_minted_a_token_for_the_agent_in_their_own_corner(
        self, gateway: TestClient
    ) -> None:
        """The web door their own `pinecall run` declared is theirs to talk through."""
        with an_app_on(gateway, BERNAS_KEY) as bernas:
            holding(bernas)
            status, said = minted(gateway, {"agent": AGENT}, bearer=BERNAS_KEY)
            assert status == 201, said
            # And not Carla's: nobody holds it in her corner, and the org's own is empty too.
            status, said = minted(gateway, {"agent": AGENT}, bearer=CARLAS_KEY)
            assert status == 404, said

    def test_a_developer_reads_the_routes_their_own_run_declared(self, gateway: TestClient) -> None:
        with an_app_on(gateway, BERNAS_KEY) as bernas:
            holding(bernas, a_door("phone", A_NUMBER))
            handle: Any = gateway
            doors: Any = handle.get("/v1/routes", headers={"Authorization": f"Bearer {BERNAS_KEY}"})
            assert [door["number"] for door in doors.json()] == [A_NUMBER]

    async def test_the_agent_quota_is_the_orgs_across_every_corner(
        self, gateway: TestClient, orgs: MemoryOrgs
    ) -> None:
        """Two developers each holding a different agent are two agents against the plan."""
        await orgs.set_quotas(A_RECORD.org, Quotas(agents=1))
        with an_app_on(gateway, BERNAS_KEY) as bernas, an_app_on(gateway, CARLAS_KEY) as carlas:
            holding(bernas)
            carlas.send_json(a_register("otra-clinica", a_door("web")))
            refused = entry_until(carlas, "error")
            assert "1 of its 1 agents" in refused["data"]["message"]
            # The same agent in Carla's corner is not one more: a slug is counted once.
            assert holding(carlas)["type"] == "agent.registered"
