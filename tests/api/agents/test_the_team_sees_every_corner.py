"""`GET /v1/agents`: a developer sees their corner, an admin the team's, each row saying whose."""

from __future__ import annotations

from typing import Any

import pytest
from starlette.testclient import TestClient, WebSocketTestSession

from pinecall.api._deps import NOT_A_COLLEAGUE
from pinecall.auth.keys import CANNOT_LOOK_THERE, KeyRecord, MemoryKeys, held_by, looking_into
from pinecall.auth.members import MemoryMembers
from pinecall.types import ROLE_SCOPES, SANDBOX, Member
from tests.api.conftest import A_RECORD, AGENT, APPS
from tests.api.talking import a_door, a_register, got

pytestmark = pytest.mark.unit

BERNA, CARLA = "m_berna", "m_carla"
BERNAS_ADDRESS, CARLAS_ADDRESS = "berna@clinica.test", "carla@clinica.test"

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


@pytest.fixture
def members() -> MemoryMembers:
    """The two developers, so a row can say whose corner it is in words a person reads."""
    return MemoryMembers(
        [_a_person_row(BERNA, BERNAS_ADDRESS), _a_person_row(CARLA, CARLAS_ADDRESS)]
    )


def _a_person_row(id: str, email: str) -> Member:
    return Member(
        id=id, org=A_RECORD.org, email=email, name=email, role="developer", status="active"
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
            {
                "slug": AGENT,
                "channels": ["web"],
                "holder": {"holder": BERNA, "name": BERNAS_ADDRESS},
            }
        ]
        assert _listed(gateway, CARLAS_KEY) == [
            {
                "slug": AGENT,
                "channels": ["web"],
                "holder": {"holder": CARLA, "name": CARLAS_ADDRESS},
            }
        ]


def test_an_admin_sees_every_corner_and_each_row_says_whose(gateway: TestClient) -> None:
    """Nobody could see this: a tenant's admin had no way to tell what the team was running.

    And the id alone would not have helped: `m_berna` names nobody, so the row carries the same
    `{holder, name}` the line door answers with.
    """
    with _an_app_on(gateway, BERNAS_KEY) as bernas, _an_app_on(gateway, CARLAS_KEY) as carlas:
        bernas.send_json(a_register(AGENT, a_door("web")))
        bernas.receive_json()
        carlas.send_json(a_register(AGENT, a_door("web")))
        carlas.receive_json()

        seen = _listed(gateway, AN_ADMINS_KEY)

        whose = sorted(str(dict(held["holder"])["name"]) for held in seen)  # type: ignore[arg-type]
        assert whose == [BERNAS_ADDRESS, CARLAS_ADDRESS]
        assert {str(held["slug"]) for held in seen} == {AGENT}


def _the_line(gateway: TestClient, key: str, corner: str | None) -> tuple[int, Any]:
    headers = {"Authorization": f"Bearer {key}"}
    if corner is not None:
        headers["pinecall-corner"] = corner
    answer = gateway.get(f"/v1/agents/{AGENT}/line", headers=headers)
    return answer.status_code, answer.json()


def test_an_admin_opens_a_developers_copy_and_every_door_answers_in_that_corner(
    gateway: TestClient,
) -> None:
    """The console's agent panel: an admin picks Carla's copy, and the doors are Carla's."""
    with _an_app_on(gateway, BERNAS_KEY) as bernas, _an_app_on(gateway, CARLAS_KEY) as carlas:
        bernas.send_json(a_register(AGENT, a_door("web")))
        bernas.receive_json()
        carlas.send_json(a_register(AGENT, a_door("web")))
        carlas.receive_json()

        _, own = _the_line(gateway, AN_ADMINS_KEY, None)
        _, as_berna = _the_line(gateway, AN_ADMINS_KEY, BERNA)
        _, as_carla = _the_line(gateway, AN_ADMINS_KEY, CARLA)

    # Berna rang first and holds the line: it is hers when the admin looks from her corner only.
    assert (own["yours"], as_berna["yours"], as_carla["yours"]) == (False, True, False)


def test_a_developer_cannot_open_a_colleagues_copy(gateway: TestClient) -> None:
    status, body = _the_line(gateway, BERNAS_KEY, CARLA)
    assert (status, body["detail"]) == (403, CANNOT_LOOK_THERE)


def test_an_admin_names_only_a_member_of_the_org(gateway: TestClient) -> None:
    status, body = _the_line(gateway, AN_ADMINS_KEY, "m_nobody")
    assert (status, body["detail"]) == (403, NOT_A_COLLEAGUE)


def test_production_has_no_corner_to_open() -> None:
    admin = KeyRecord(key_id="k", org=A_RECORD.org, env="production", scopes=ROLE_SCOPES["admin"])
    with pytest.raises(PermissionError):
        looking_into(admin, CARLA)
    sandbox = KeyRecord(
        key_id="k", org=A_RECORD.org, env=SANDBOX, subject="m_ana", scopes=ROLE_SCOPES["admin"]
    )
    assert held_by(looking_into(sandbox, CARLA)) == CARLA
