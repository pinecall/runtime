"""/v1/apps: every app holding an org's agents right now, where it runs, and a stop for one."""

from __future__ import annotations

from typing import Any

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from pinecall.api.agents.apps import NO_SUCH_APP
from pinecall.auth.keys import KeyRecord, MemoryKeys
from tests.api.conftest import A_KEY, A_RECORD, AGENT, APPS
from tests.api.talking import a_door, a_frame, got

pytestmark = pytest.mark.unit

ANOTHERS_KEY = "pc_live_another_tenant"
ANOTHER = KeyRecord(key_id="k_other", org="tienda-sur", label="their server")


@pytest.fixture
def keys() -> MemoryKeys:
    return MemoryKeys({A_KEY: A_RECORD, ANOTHERS_KEY: ANOTHER})


def registering(host: str) -> dict[str, object]:
    """The register an app sends, naming the machine it runs on."""
    return a_frame(
        "agent.register",
        AGENT,
        {"routes": [a_door("web")], "sdk": "pinecall/0.5.1", "host": host},
    )


def stopped(gateway: TestClient, app: str, bearer: str = A_KEY) -> tuple[int, Any]:
    handle: Any = gateway
    answer: Any = handle.post(f"/v1/apps/{app}/stop", headers={"Authorization": f"Bearer {bearer}"})
    return int(answer.status_code), answer.json()


def test_two_processes_holding_one_agent_are_two_rows_each_saying_where_it_runs(
    gateway: TestClient,
) -> None:
    auth = {"Authorization": f"Bearer {A_KEY}"}
    with gateway.websocket_connect(APPS, headers=auth) as server:
        server.send_json(registering("clinica-norte-web-1"))
        first = server.receive_json()["data"]["app"]
        with gateway.websocket_connect(APPS, headers=auth) as laptop:
            laptop.send_json(registering("berna-air"))
            second = laptop.receive_json()["data"]["app"]
            status, listed = got(gateway, "/v1/apps")
    assert status == 200
    rows = {row["app"]: row for row in listed["apps"]}
    assert list(rows) == [first, second], "oldest connection first"
    assert (rows[second]["host"], rows[second]["sdk"], rows[second]["agents"]) == (
        "berna-air",
        "pinecall/0.5.1",
        [AGENT],
    )
    assert (rows[first]["env"], rows[first]["holder"]) == ("production", None)
    assert rows[first]["connected_at"] <= rows[second]["connected_at"]


def test_a_stopped_app_hears_why_and_is_gone_from_the_list(gateway: TestClient) -> None:
    auth = {"Authorization": f"Bearer {A_KEY}"}
    with gateway.websocket_connect(APPS, headers=auth) as laptop:
        laptop.send_json(registering("berna-air"))
        app = laptop.receive_json()["data"]["app"]
        assert stopped(gateway, app) == (200, {"app": app, "stopped": True})
        said = laptop.receive_json()
        assert (said["type"], said["data"]["code"]) == ("error", "stopped")
        assert said["data"]["recoverable"] is False
        with pytest.raises(WebSocketDisconnect):
            laptop.receive_json()
    assert got(gateway, "/v1/apps")[1]["apps"] == []


def test_another_orgs_app_is_not_there_to_stop_or_to_see(gateway: TestClient) -> None:
    with gateway.websocket_connect(APPS, headers={"Authorization": f"Bearer {A_KEY}"}) as mine:
        mine.send_json(registering("clinica-norte-web-1"))
        app = mine.receive_json()["data"]["app"]
        assert got(gateway, "/v1/apps", ANOTHERS_KEY)[1]["apps"] == []
        status, said = stopped(gateway, app, ANOTHERS_KEY)
        assert (status, said["detail"]) == (404, NO_SUCH_APP.format(app=app))
        assert len(got(gateway, "/v1/apps")[1]["apps"]) == 1, "and it is still connected"
