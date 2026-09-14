"""`GET /v1/agents`: a developer sees their corner, an admin the team's, each row saying whose."""

from __future__ import annotations

from typing import Any

import pytest
from starlette.testclient import TestClient, WebSocketTestSession

from pinecall.auth.keys import KeyRecord, MemoryKeys
from pinecall.types import ROLE_SCOPES, SANDBOX
from tests.api.conftest import A_RECORD, AGENT, APPS
from tests.api.talking import a_door, a_register, got

pytestmark = pytest.mark.unit

BERNA, CARLA = "m_berna", "m_carla"

# One org, three keys into the SAME world. The two laptops are people; the third is the admin's
# browser, which holds no agent of its own and is the one that has to see both.
BERNAS_KEY = "pk_test_bernas_laptop"
CARLAS_KEY = "pk_test_carlas_laptop"
AN_ADMINS_KEY = "pk_test_the_admins_browser"


def _a_person(key_id: str, subject: str, role: str) -> KeyRecord:
    return KeyRecord(
        key_id=key_id, org=A_RECORD.org, env=SANDBOX, subject=subject, scopes=ROLE_SCOPES[role]
    )


@pytest.fixture
def keys() -> MemoryKeys:
    return MemoryKeys(
        {
            BERNAS_KEY: _a_person("k_berna", BERNA, "developer"),
            CARLAS_KEY: _a_person("k_carla", CARLA, "developer"),
            AN_ADMINS_KEY: _a_person("k_admin", "m_ana", "admin"),
        }
    )


def _an_app_on(gateway: TestClient, key: str) -> WebSocketTestSession:
    return gateway.websocket_connect(APPS, headers={"Authorization": f"Bearer {key}"})


def _listed(gateway: TestClient, key: str) -> list[dict[str, Any]]:
    status, body = got(gateway, "/v1/agents", key)
    assert status == 200
    return [dict(held) for held in body["agents"]]  # type: ignore[call-overload,index]


def test_a_developer_sees_their_own_corner_and_not_a_colleagues(gateway: TestClient) -> None:
    """Two laptops holding one slug: each terminal's console draws the agent it is running."""
    with _an_app_on(gateway, BERNAS_KEY) as bernas, _an_app_on(gateway, CARLAS_KEY) as carlas:
        bernas.send_json(a_register(AGENT, a_door("web")))
        bernas.receive_json()
        carlas.send_json(a_register(AGENT, a_door("web")))
        carlas.receive_json()

        assert _listed(gateway, BERNAS_KEY) == [
            {"slug": AGENT, "channels": ["web"], "holder": BERNA}
        ]
        assert _listed(gateway, CARLAS_KEY) == [
            {"slug": AGENT, "channels": ["web"], "holder": CARLA}
        ]


def test_an_admin_sees_every_corner_and_each_row_says_whose(gateway: TestClient) -> None:
    """Nobody could see this: a tenant's admin had no way to tell what the team was running."""
    with _an_app_on(gateway, BERNAS_KEY) as bernas, _an_app_on(gateway, CARLAS_KEY) as carlas:
        bernas.send_json(a_register(AGENT, a_door("web")))
        bernas.receive_json()
        carlas.send_json(a_register(AGENT, a_door("web")))
        carlas.receive_json()

        seen = _listed(gateway, AN_ADMINS_KEY)

        assert sorted(str(held["holder"]) for held in seen) == [BERNA, CARLA]
        assert {str(held["slug"]) for held in seen} == {AGENT}
